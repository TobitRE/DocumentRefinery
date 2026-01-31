import json
import os
import time
from pathlib import Path

from celery import chain, shared_task
from django.conf import settings
from django.utils import timezone

from clamav_client import clamd
from docling.datamodel.document import DoclingDocument, DoclingVersion
from docling.document_converter import DocumentConverter

from .models import (
    Artifact,
    ArtifactKind,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    Document,
)


def start_ingestion_pipeline(job_id: int):
    return chain(
        scan_pdf_task.s(job_id),
        docling_convert_task.s(),
        export_artifacts_task.s(),
        finalize_job_task.s(),
    ).apply_async()


@shared_task(bind=True)
def cleanup_expired_artifacts(self) -> int:
    now = timezone.now()
    expired_artifacts = Artifact.objects.filter(expires_at__lt=now)
    deleted = 0
    for artifact in expired_artifacts:
        abs_path = os.path.join(settings.DATA_ROOT, artifact.storage_relpath)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except OSError:
            pass
        artifact.delete()
        deleted += 1
    return deleted


@shared_task(bind=True)
def cleanup_expired_documents(self) -> int:
    now = timezone.now()
    expired_docs = Document.objects.filter(expires_at__lt=now)
    deleted = 0
    for doc in expired_docs:
        artifacts = Artifact.objects.filter(job__document=doc)
        for artifact in artifacts:
            abs_path = os.path.join(settings.DATA_ROOT, artifact.storage_relpath)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError:
                pass
        artifacts.delete()
        for path in filter(None, [doc.get_quarantine_path(), doc.get_clean_path()]):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        doc.delete()
        deleted += 1
    return deleted


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _write_artifact(job: IngestionJob, kind: str, relpath: str, data: bytes, content_type: str):
    abs_path = Path(settings.DATA_ROOT) / relpath
    _write_bytes_atomic(abs_path, data)
    checksum = _sha256_bytes(data)
    size_bytes = len(data)
    return Artifact.objects.create(
        tenant=job.tenant,
        created_by_key=job.created_by_key,
        job=job,
        kind=kind,
        storage_relpath=relpath,
        checksum_sha256=checksum,
        size_bytes=size_bytes,
        content_type=content_type,
    )


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _artifact_relpath(job: IngestionJob, filename: str) -> str:
    return os.path.join("artifacts", str(job.tenant_id), str(job.id), filename)


def _mark_failed(job: IngestionJob, code: str, message: str, details: dict | None = None):
    job.status = IngestionJobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.error_details_json = details or {}
    job.finished_at = timezone.now()
    job.recompute_durations()
    job.save()


def _is_canceled(job: IngestionJob) -> bool:
    return job.status == IngestionJobStatus.CANCELED


@shared_task(bind=True)
def scan_pdf_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    job.stage = IngestionStage.SCANNING
    if not job.started_at:
        job.started_at = timezone.now()
    job.status = IngestionJobStatus.RUNNING
    job.save()

    start = time.monotonic()
    document = job.document
    abs_path = document.get_quarantine_path()

    try:
        scanner = clamd.ClamdNetworkSocket(settings.CLAMAV_HOST, settings.CLAMAV_PORT)
        results = scanner.scan(abs_path)
    except Exception as exc:
        _mark_failed(job, "CLAMAV_UNAVAILABLE", str(exc))
        raise

    status, reason = results.get(abs_path, ("ERROR", "No scan result"))
    if status == "FOUND":
        document.status = DocumentStatus.INFECTED
        document.save()
        job.status = IngestionJobStatus.QUARANTINED
        job.error_code = "VIRUS_FOUND"
        job.error_message = reason or "Virus detected"
        job.finished_at = timezone.now()
        job.recompute_durations()
        job.save()
        raise RuntimeError("Virus found")
    if status == "ERROR":
        _mark_failed(job, "VIRUS_SCAN_ERROR", reason or "Scan error")
        raise RuntimeError("Virus scan error")

    clean_relpath = os.path.join(
        "uploads", "clean", str(job.tenant_id), f"{document.uuid}.pdf"
    )
    clean_abspath = os.path.join(settings.DATA_ROOT, clean_relpath)
    os.makedirs(os.path.dirname(clean_abspath), exist_ok=True)
    os.replace(abs_path, clean_abspath)

    document.status = DocumentStatus.CLEAN
    document.storage_relpath_clean = clean_relpath
    document.save()

    job.scan_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


@shared_task(bind=True)
def docling_convert_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    job.stage = IngestionStage.CONVERTING
    job.status = IngestionJobStatus.RUNNING
    job.save()

    start = time.monotonic()
    document = job.document

    max_pages = job.options_json.get("max_num_pages") if job.options_json else None
    if not max_pages and settings.MAX_PAGES > 0:
        max_pages = settings.MAX_PAGES
    max_pages = max_pages or 9223372036854775807

    max_file_size = job.options_json.get("max_file_size") if job.options_json else None
    if not max_file_size:
        max_file_size = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024

    try:
        converter = DocumentConverter()
        result = converter.convert(
            document.get_clean_path(),
            max_num_pages=max_pages,
            max_file_size=max_file_size,
        )
        docling_doc = result.document
    except Exception as exc:
        _mark_failed(job, "DOCLING_CONVERT_FAILED", str(exc))
        raise

    relpath = _artifact_relpath(job, "docling.json")
    payload = json.dumps(docling_doc.export_to_dict(), ensure_ascii=False, indent=2).encode(
        "utf-8"
    )
    _write_artifact(job, ArtifactKind.DOCLING_JSON, relpath, payload, "application/json")

    job.docling_version = DoclingVersion().docling_version
    job.convert_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


@shared_task(bind=True)
def export_artifacts_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    job.stage = IngestionStage.EXPORTING
    job.status = IngestionJobStatus.RUNNING
    job.save()

    start = time.monotonic()
    relpath = _artifact_relpath(job, "docling.json")
    abs_path = Path(settings.DATA_ROOT) / relpath

    try:
        with open(abs_path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        docling_doc = DoclingDocument.model_validate(data)
    except Exception as exc:
        _mark_failed(job, "DOCLING_LOAD_FAILED", str(exc))
        raise

    exports = job.options_json.get("exports") if job.options_json else None
    if not exports:
        exports = ["markdown", "text", "doctags"]

    if "markdown" in exports:
        markdown = docling_doc.export_to_markdown()
        _write_artifact(
            job,
            ArtifactKind.MARKDOWN,
            _artifact_relpath(job, "document.md"),
            markdown.encode("utf-8"),
            "text/markdown",
        )
    if "text" in exports:
        text = docling_doc.export_to_text()
        _write_artifact(
            job,
            ArtifactKind.TEXT,
            _artifact_relpath(job, "document.txt"),
            text.encode("utf-8"),
            "text/plain",
        )
    if "doctags" in exports:
        doctags = docling_doc.export_to_doctags()
        _write_artifact(
            job,
            ArtifactKind.DOCTAGS,
            _artifact_relpath(job, "document.doctags"),
            doctags.encode("utf-8"),
            "text/plain",
        )

    job.export_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


@shared_task(bind=True)
def finalize_job_task(self, job_id: int) -> int:
    job = IngestionJob.objects.get(pk=job_id)
    if _is_canceled(job):
        return job_id
    job.stage = IngestionStage.FINALIZING
    job.status = IngestionJobStatus.SUCCEEDED
    job.finished_at = timezone.now()
    job.recompute_durations()
    job.save()
    return job_id
