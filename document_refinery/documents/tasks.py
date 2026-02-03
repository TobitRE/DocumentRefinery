import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import timedelta
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
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)

DEFAULT_WEBHOOK_EVENTS = ["job.updated"]


def _webhook_max_attempts() -> int:
    return int(getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 5))


def _webhook_initial_backoff_seconds() -> int:
    return int(getattr(settings, "WEBHOOK_INITIAL_BACKOFF_SECONDS", 30))


def _webhook_request_timeout() -> int:
    return int(getattr(settings, "WEBHOOK_REQUEST_TIMEOUT", 10))


def start_ingestion_pipeline(job_id: int):
    result = chain(
        scan_pdf_task.s(job_id),
        docling_convert_task.s(),
        export_artifacts_task.s(),
        finalize_job_task.s(),
    ).apply_async(queue=settings.CELERY_DEFAULT_QUEUE)
    try:
        job = IngestionJob.objects.filter(pk=job_id).first()
        if job:
            root = result
            while getattr(root, "parent", None):
                root = root.parent
            job.celery_task_id = root.id
            job.save(update_fields=["celery_task_id"])
    except Exception:
        pass
    return result


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
    prev_status = job.status
    prev_stage = job.stage
    job.status = IngestionJobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.error_details_json = details or {}
    job.finished_at = timezone.now()
    job.recompute_durations()
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)


def _is_canceled(job: IngestionJob) -> bool:
    return job.status == IngestionJobStatus.CANCELED


@shared_task(bind=True)
def scan_pdf_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.SCANNING
    if not job.started_at:
        job.started_at = timezone.now()
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

    start = time.monotonic()
    document = job.document
    abs_path = document.get_quarantine_path()

    try:
        scanner = clamd.ClamdNetworkSocket(settings.CLAMAV_HOST, settings.CLAMAV_PORT)
        results = scanner.scan(abs_path)
    except Exception as exc:
        _mark_failed(job, "CLAMAV_UNAVAILABLE", str(exc))
        raise
    if not isinstance(results, dict):
        _mark_failed(job, "CLAMAV_INVALID_RESPONSE", "Invalid scan response from ClamAV")
        raise RuntimeError("ClamAV invalid response")

    status, reason = results.get(abs_path, ("ERROR", "No scan result"))
    if status == "FOUND":
        document.status = DocumentStatus.INFECTED
        document.save()
        prev_status = job.status
        prev_stage = job.stage
        job.status = IngestionJobStatus.QUARANTINED
        job.error_code = "VIRUS_FOUND"
        job.error_message = reason or "Virus detected"
        job.finished_at = timezone.now()
        job.recompute_durations()
        job.save()
        queue_job_webhooks(job, prev_status, prev_stage)
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
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.CONVERTING
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

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
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.EXPORTING
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

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
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.FINALIZING
    job.status = IngestionJobStatus.SUCCEEDED
    job.finished_at = timezone.now()
    job.recompute_durations()
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)
    return job_id


def _isoformat(value):
    if not value:
        return None
    return value.isoformat()


def _job_webhook_payload(job: IngestionJob, prev_status: str | None, prev_stage: str | None) -> dict:
    return {
        "event": "job.updated",
        "job_id": job.id,
        "job_uuid": str(job.uuid),
        "document_id": job.document_id,
        "external_uuid": str(job.external_uuid) if job.external_uuid else None,
        "status": job.status,
        "stage": job.stage,
        "previous_status": prev_status,
        "previous_stage": prev_stage,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_details": job.error_details_json,
        "queued_at": _isoformat(job.queued_at),
        "started_at": _isoformat(job.started_at),
        "finished_at": _isoformat(job.finished_at),
        "created_at": _isoformat(job.created_at),
        "modified_at": _isoformat(job.modified_at),
    }


def queue_job_webhooks(job: IngestionJob, prev_status: str | None, prev_stage: str | None) -> int:
    if prev_status == job.status and prev_stage == job.stage:
        return 0
    endpoints = WebhookEndpoint.objects.filter(tenant=job.tenant, enabled=True)
    if not endpoints.exists():
        return 0
    payload = _job_webhook_payload(job, prev_status, prev_stage)
    delivery_ids = []
    for endpoint in endpoints:
        events = endpoint.events or DEFAULT_WEBHOOK_EVENTS
        if "job.updated" not in events:
            continue
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json=payload,
            status=WebhookDeliveryStatus.PENDING,
        )
        delivery_ids.append(delivery.id)
    for delivery_id in delivery_ids:
        deliver_webhook_delivery.delay(delivery_id)
    return len(delivery_ids)


@shared_task(bind=True)
def deliver_webhook_delivery(self, delivery_id: int) -> bool:
    delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
    if delivery.status == WebhookDeliveryStatus.DELIVERED:
        return True
    if delivery.status == WebhookDeliveryStatus.FAILED:
        return False

    endpoint = delivery.endpoint
    if not endpoint.enabled:
        delivery.status = WebhookDeliveryStatus.FAILED
        delivery.last_error = "Endpoint disabled"
        delivery.save(update_fields=["status", "last_error", "modified_at"])
        return False

    payload = delivery.payload_json or {}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "DocumentRefinery-Webhooks/1.0",
        "X-DocRefinery-Event": delivery.event_type,
        "X-DocRefinery-Delivery": str(delivery.uuid),
    }
    if endpoint.secret:
        signature = hmac.new(endpoint.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-DocRefinery-Signature"] = f"sha256={signature}"

    request = urllib.request.Request(endpoint.url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_webhook_request_timeout()) as response:
            code = response.getcode()
        if 200 <= code < 300:
            delivery.status = WebhookDeliveryStatus.DELIVERED
            delivery.response_code = code
            delivery.delivered_at = timezone.now()
            delivery.next_retry_at = None
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "delivered_at",
                    "next_retry_at",
                    "modified_at",
                ]
            )
            endpoint.last_success_at = timezone.now()
            endpoint.save(update_fields=["last_success_at", "modified_at"])
            return True
        raise urllib.error.HTTPError(
            endpoint.url, code, f"Unexpected status {code}", hdrs=None, fp=None
        )
    except Exception as exc:
        delivery.attempt += 1
        response_code = getattr(exc, "code", None)
        delivery.response_code = response_code
        delivery.last_error = str(exc)
        if delivery.attempt >= _webhook_max_attempts():
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.next_retry_at = None
        else:
            delivery.status = WebhookDeliveryStatus.RETRYING
            delay = _webhook_initial_backoff_seconds() * (2 ** (delivery.attempt - 1))
            delivery.next_retry_at = timezone.now() + timedelta(seconds=delay)
        delivery.save(
            update_fields=[
                "status",
                "attempt",
                "response_code",
                "last_error",
                "next_retry_at",
                "modified_at",
            ]
        )
        endpoint.last_failure_at = timezone.now()
        endpoint.save(update_fields=["last_failure_at", "modified_at"])
        if delivery.status == WebhookDeliveryStatus.RETRYING:
            if not getattr(self.request, "called_directly", False) and not getattr(
                self.request, "is_eager", False
            ):
                self.apply_async(args=[delivery.id], countdown=delay)
        return False
