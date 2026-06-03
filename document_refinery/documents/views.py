import json
import hashlib
import os
import shutil
import uuid
import zipfile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import FileResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from celery import current_app

from authn.permissions import HasScope
from authn.permissions import APIKeyRequired
from authn.options import DEFAULT_ALLOWED_UPLOAD_MIME_TYPES, validate_docling_options

from django.db import IntegrityError

from .models import (
    Artifact,
    ArtifactKind,
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    WebhookEndpoint,
)
from .tasks import queue_job_webhooks, start_ingestion_pipeline
from .docling_options import (
    apply_profile_overrides,
    capabilities_payload,
    normalize_docling_options,
    profile_catalog,
    resolve_effective_options,
)
from .serializers import (
    ArtifactSerializer,
    DoclingOptionsResolveSerializer,
    DocumentSerializer,
    DocumentCompareSerializer,
    DocumentIngestSerializer,
    DocumentUploadSerializer,
    JobSerializer,
    WebhookEndpointSerializer,
)

ARTIFACT_PREVIEW_BYTES = 256 * 1024
ARTIFACT_PREVIEW_ZIP_ENTRIES = 200
DOCLING_METADATA_SCOPES = {"dashboard:read", "documents:write"}


def _has_any_scope(api_key, scopes: set[str]) -> bool:
    return bool(set(api_key.scopes or []) & scopes)


def _scope_denied_response() -> Response:
    return Response(
        {"error_code": "INSUFFICIENT_SCOPE", "message": "API key scope is insufficient."},
        status=status.HTTP_403_FORBIDDEN,
    )


def _looks_like_pdf(uploaded) -> bool:
    try:
        header = uploaded.read(5)
        uploaded.seek(0)
    except Exception:
        return False
    return header == b"%PDF-"


def _safe_remove_file(path: str) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _queue_unavailable_response():
    return Response(
        {
            "error_code": "QUEUE_UNAVAILABLE",
            "message": "Ingestion queue is unavailable. Please retry later.",
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _latest_job_for_document(document: Document) -> IngestionJob | None:
    return (
        IngestionJob.objects.filter(tenant=document.tenant, document=document)
        .order_by("-created_at", "-id")
        .first()
    )


def _duplicate_location(request, document: Document) -> str:
    return request.build_absolute_uri(f"{request.path.rstrip('/')}/{document.id}/")


def _duplicate_document_payload(document: Document) -> dict[str, object]:
    latest_job = _latest_job_for_document(document)
    return {
        "error_code": "DUPLICATE_DOCUMENT",
        "message": "Document already exists.",
        "duplicate": True,
        "document_id": document.id,
        "document_uuid": str(document.uuid),
        "sha256": document.sha256,
        "latest_job_id": latest_job.id if latest_job else None,
        "latest_job_uuid": str(latest_job.uuid) if latest_job else None,
        "latest_job_status": latest_job.status if latest_job else None,
    }


def _latest_job_summary_payload(job: IngestionJob | None) -> dict[str, object] | None:
    if not job:
        return None
    return {
        "id": job.id,
        "uuid": str(job.uuid),
        "status": job.status,
        "stage": job.stage,
    }


def _duplicate_document_response(document: Document, request, duplicate_policy: str) -> Response:
    latest_job = _latest_job_for_document(document)
    if duplicate_policy == "return_existing":
        response = Response(
            {
                "duplicate": True,
                "document": DocumentSerializer(document).data,
                "latest_job": _latest_job_summary_payload(latest_job),
            },
            status=status.HTTP_200_OK,
        )
    else:
        response = Response(
            _duplicate_document_payload(document),
            status=status.HTTP_409_CONFLICT,
        )
    response["Location"] = _duplicate_location(request, document)
    return response


def _build_ingestion_options(api_key, options_json, profile: str | None) -> dict:
    resolved = resolve_effective_options(api_key, options_json, profile)
    validate_docling_options(resolved["effective_options"])
    return resolved["effective_options"] or {}


def _resolve_document_source_abs(document: Document) -> str:
    clean_path = document.get_clean_path()
    if clean_path and os.path.exists(clean_path):
        return clean_path
    quarantine_path = document.get_quarantine_path()
    if quarantine_path and os.path.exists(quarantine_path):
        return quarantine_path
    return ""


def _copy_document_source_for_job(document: Document) -> tuple[str, str] | None:
    source_abs = _resolve_document_source_abs(document)
    if not source_abs:
        return None
    relpath = os.path.join(
        "uploads",
        "quarantine",
        str(document.tenant_id),
        f"{document.uuid}-{uuid.uuid4()}.pdf",
    )
    abs_path = os.path.join(settings.DATA_ROOT, relpath)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    shutil.copy2(source_abs, abs_path)
    return relpath, abs_path


def _create_ingestion_job(
    api_key,
    document: Document,
    *,
    options_json: dict,
    profile: str | None,
    source_relpath: str = "",
) -> IngestionJob:
    return IngestionJob.objects.create(
        tenant=api_key.tenant,
        created_by_key=api_key,
        document=document,
        external_uuid=document.external_uuid,
        profile=profile,
        source_relpath=source_relpath,
        status=IngestionJobStatus.QUEUED,
        stage=IngestionStage.SCANNING,
        queued_at=timezone.now(),
        options_json=options_json or {},
    )


def _ingest_job_payload(
    document: Document,
    job: IngestionJob,
    *,
    mode: str,
    created: bool,
    reused: bool = False,
    retried: bool = False,
) -> dict[str, object]:
    return {
        "mode": mode,
        "created": created,
        "reused": reused,
        "retried": retried,
        "document": DocumentSerializer(document).data,
        "job": _latest_job_summary_payload(job),
        "job_id": job.id,
        "job_uuid": str(job.uuid),
    }


def _options_match_job_snapshot(job_options: dict | None, effective_options: dict, profile: str | None) -> bool:
    job_options = job_options or {}
    if job_options == (effective_options or {}):
        return True
    if not profile:
        return False
    try:
        legacy_effective = apply_profile_overrides(job_options, profile)
        legacy_effective, _warnings = normalize_docling_options(legacy_effective)
    except Exception:
        return False
    return legacy_effective == (effective_options or {})


def _matching_jobs(document: Document, profile: str | None, options_json: dict):
    candidates = IngestionJob.objects.filter(
        tenant=document.tenant,
        document=document,
        profile=profile,
    ).order_by("-created_at", "-id")
    compatible_ids = [
        job.id
        for job in candidates
        if _options_match_job_snapshot(job.options_json, options_json or {}, profile)
    ]
    return candidates.filter(id__in=compatible_ids)


def _missing_source_response() -> Response:
    return Response(
        {"error_code": "MISSING_SOURCE_FILE", "message": "Document file is missing."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _stash_job_artifacts_for_retry(job: IngestionJob) -> list[dict[str, object]]:
    snapshots = []
    artifacts = list(Artifact.objects.filter(job=job))
    try:
        for artifact in artifacts:
            abs_path = os.path.join(settings.DATA_ROOT, artifact.storage_relpath)
            backup_path = ""
            if os.path.exists(abs_path):
                backup_path = f"{abs_path}.retry-backup-{uuid.uuid4().hex}"
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                os.replace(abs_path, backup_path)
            snapshots.append(
                {
                    "id": artifact.id,
                    "uuid": artifact.uuid,
                    "created_at": artifact.created_at,
                    "modified_at": artifact.modified_at,
                    "tenant_id": artifact.tenant_id,
                    "created_by_key_id": artifact.created_by_key_id,
                    "job_id": artifact.job_id,
                    "kind": artifact.kind,
                    "storage_relpath": artifact.storage_relpath,
                    "checksum_sha256": artifact.checksum_sha256,
                    "size_bytes": artifact.size_bytes,
                    "content_type": artifact.content_type,
                    "expires_at": artifact.expires_at,
                    "abs_path": abs_path,
                    "backup_path": backup_path,
                }
            )
        if artifacts:
            Artifact.objects.filter(pk__in=[artifact.pk for artifact in artifacts]).delete()
    except Exception:
        for snapshot in snapshots:
            backup_path = str(snapshot["backup_path"])
            abs_path = str(snapshot["abs_path"])
            if backup_path and os.path.exists(backup_path):
                os.replace(backup_path, abs_path)
        raise
    return snapshots


def _restore_stashed_artifacts(snapshots: list[dict[str, object]]) -> None:
    for snapshot in snapshots:
        backup_path = str(snapshot["backup_path"])
        abs_path = str(snapshot["abs_path"])
        if backup_path and os.path.exists(backup_path):
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            os.replace(backup_path, abs_path)
        fields = {
            key: value
            for key, value in snapshot.items()
            if key not in {"abs_path", "backup_path"}
        }
        Artifact.objects.create(**fields)


def _discard_stashed_artifacts(snapshots: list[dict[str, object]]) -> None:
    for snapshot in snapshots:
        _safe_remove_file(str(snapshot["backup_path"]))


def _revoke_job_pipeline(job_id: int) -> None:
    task_id = (
        IngestionJob.objects.filter(pk=job_id).values_list("celery_task_id", flat=True).first()
        or ""
    )
    if not task_id:
        return
    try:
        current_app.control.revoke(
            task_id,
            terminate=True,
            signal=settings.CELERY_CANCEL_SIGNAL,
        )
    except Exception:
        pass


def _rollback_created_jobs(jobs: list[IngestionJob], source_paths: list[str] | None = None) -> None:
    for job in jobs:
        _revoke_job_pipeline(job.id)
        stored_relpath = (
            IngestionJob.objects.filter(pk=job.id).values_list("source_relpath", flat=True).first()
            or job.source_relpath
            or ""
        )
        job.delete()
        if stored_relpath:
            _safe_remove_file(os.path.join(settings.DATA_ROOT, stored_relpath))

    for path in source_paths or []:
        _safe_remove_file(path)


def _retry_snapshot(job: IngestionJob) -> dict[str, object]:
    return {
        "attempt": job.attempt,
        "status": job.status,
        "stage": job.stage,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_details_json": job.error_details_json,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_ms": job.duration_ms,
        "scan_ms": job.scan_ms,
        "convert_ms": job.convert_ms,
        "export_ms": job.export_ms,
        "chunk_ms": job.chunk_ms,
        "docling_version": job.docling_version,
        "docling_core_version": job.docling_core_version,
        "docling_parse_version": job.docling_parse_version,
        "runtime_json": job.runtime_json,
        "result_metrics_json": job.result_metrics_json,
        "celery_task_id": job.celery_task_id,
        "worker_hostname": job.worker_hostname,
        "source_relpath": job.source_relpath,
    }


def _restore_retry_snapshot(job: IngestionJob, snapshot: dict[str, object]) -> None:
    for field, value in snapshot.items():
        setattr(job, field, value)
    job.save(update_fields=list(snapshot.keys()))


def _retry_job(
    job: IngestionJob,
    *,
    source_relpath: str | None = None,
    source_abs_path: str | None = None,
) -> Response:
    if job.status not in (IngestionJobStatus.FAILED, IngestionJobStatus.QUARANTINED):
        if source_abs_path:
            _safe_remove_file(source_abs_path)
        return Response(
            {"error_code": "NOT_RETRYABLE", "message": "Job cannot be retried."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if job.attempt >= job.max_retries:
        if source_abs_path:
            _safe_remove_file(source_abs_path)
        return Response(
            {"error_code": "RETRY_LIMIT", "message": "Retry limit reached."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    snapshot = _retry_snapshot(job)
    prev_status = job.status
    prev_stage = job.stage
    stashed_artifacts = []
    try:
        stashed_artifacts = _stash_job_artifacts_for_retry(job)
        if source_relpath is not None:
            job.source_relpath = source_relpath
        job.attempt += 1
        job.status = IngestionJobStatus.QUEUED
        job.stage = IngestionStage.SCANNING
        job.error_code = ""
        job.error_message = ""
        job.error_details_json = None
        job.queued_at = timezone.now()
        job.started_at = None
        job.finished_at = None
        job.duration_ms = None
        job.scan_ms = None
        job.convert_ms = None
        job.export_ms = None
        job.chunk_ms = None
        job.docling_version = ""
        job.docling_core_version = ""
        job.docling_parse_version = ""
        job.runtime_json = {}
        job.result_metrics_json = {}
        job.celery_task_id = ""
        job.save()
        start_ingestion_pipeline(job.id)
    except Exception:
        _restore_retry_snapshot(job, snapshot)
        _restore_stashed_artifacts(stashed_artifacts)
        if source_abs_path:
            _safe_remove_file(source_abs_path)
        return _queue_unavailable_response()

    _discard_stashed_artifacts(stashed_artifacts)
    job.refresh_from_db()
    if job.status == IngestionJobStatus.QUEUED and job.stage == IngestionStage.SCANNING:
        queue_job_webhooks(job, prev_status, prev_stage)
    return Response(JobSerializer(job).data)


def create_document_for_api_key(api_key, data, request) -> Response:
    serializer = DocumentUploadSerializer(data=data)
    serializer.is_valid(raise_exception=True)

    uploaded = serializer.validated_data["file"]
    ingest = serializer.validated_data.get("ingest", False)
    options_json = serializer.validated_data.get("options_json", None)
    external_uuid = serializer.validated_data.get("external_uuid", None)
    profile = serializer.validated_data.get("profile", None)
    duplicate_policy = serializer.validated_data.get("duplicate_policy", "conflict")

    content_type = (uploaded.content_type or "").strip().lower()
    allowed_upload_mime_types = [
        str(item).strip().lower()
        for item in (api_key.allowed_upload_mime_types or DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)
        if str(item).strip()
    ]
    if not allowed_upload_mime_types:
        allowed_upload_mime_types = list(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)

    if content_type not in allowed_upload_mime_types:
        return Response(
            {
                "error_code": "UNSUPPORTED_MEDIA_TYPE",
                "message": (
                    "File type is not allowed for this API key. "
                    f"Allowed types: {', '.join(allowed_upload_mime_types)}."
                ),
            },
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    if content_type in ("application/pdf", "application/x-pdf") and not _looks_like_pdf(uploaded):
        return Response(
            {"error_code": "INVALID_PDF", "message": "File does not look like a PDF."},
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    max_bytes = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024
    if uploaded.size and uploaded.size > max_bytes:
        return Response(
            {"error_code": "FILE_TOO_LARGE", "message": "File exceeds size limit."},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    tenant_id = api_key.tenant_id
    filename = uploaded.name

    doc = Document(
        tenant=api_key.tenant,
        created_by_key=api_key,
        external_uuid=external_uuid,
        original_filename=filename,
        mime_type=content_type or "application/pdf",
        size_bytes=0,
        storage_relpath_quarantine="",
    )
    relpath = os.path.join("uploads", "quarantine", str(tenant_id), f"{doc.uuid}.pdf")
    abs_path = os.path.join(settings.DATA_ROOT, relpath)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    hasher = hashlib.sha256()
    size_bytes = 0
    try:
        with open(abs_path, "wb") as out:
            for chunk in uploaded.chunks():
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ValueError("file_too_large")
                hasher.update(chunk)
                out.write(chunk)
    except ValueError:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        return Response(
            {"error_code": "FILE_TOO_LARGE", "message": "File exceeds size limit."},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    doc.sha256 = hasher.hexdigest()
    doc.size_bytes = size_bytes
    doc.storage_relpath_quarantine = relpath
    existing_doc = Document.objects.filter(tenant=api_key.tenant, sha256=doc.sha256).first()
    if existing_doc:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        return _duplicate_document_response(existing_doc, request, duplicate_policy)
    try:
        doc.save()
    except IntegrityError:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        existing_doc = Document.objects.filter(tenant=api_key.tenant, sha256=doc.sha256).first()
        if existing_doc:
            return _duplicate_document_response(existing_doc, request, duplicate_policy)
        raise

    job_id = None
    if ingest:
        try:
            options_json = _build_ingestion_options(api_key, options_json, profile)
        except ValidationError as exc:
            quarantine_path = doc.get_quarantine_path()
            if quarantine_path and os.path.exists(quarantine_path):
                os.remove(quarantine_path)
            doc.delete()
            message = "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)
            return Response(
                {"error_code": "INVALID_OPTIONS", "message": message},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = _create_ingestion_job(
            api_key,
            doc,
            options_json=options_json,
            profile=profile,
        )
        job_id = job.id
        try:
            start_ingestion_pipeline(job_id)
        except Exception:
            IngestionJob.objects.filter(pk=job_id).delete()
            _safe_remove_file(doc.get_quarantine_path())
            doc.delete()
            return _queue_unavailable_response()

    payload = DocumentSerializer(doc).data
    if job_id:
        payload["job_id"] = job_id
    return Response(payload, status=status.HTTP_201_CREATED)


def ingest_document_for_api_key(api_key, document_uuid, data) -> Response:
    serializer = DocumentIngestSerializer(data=data)
    serializer.is_valid(raise_exception=True)

    document = Document.objects.filter(
        tenant=api_key.tenant,
        uuid=document_uuid,
    ).first()
    if not document:
        return Response(status=status.HTTP_404_NOT_FOUND)

    profile = serializer.validated_data.get("profile", None)
    mode = serializer.validated_data.get("mode", "reuse_existing")
    if mode == "retry_failed" and not _has_any_scope(api_key, {"jobs:write"}):
        return _scope_denied_response()
    try:
        options_json = _build_ingestion_options(
            api_key,
            serializer.validated_data.get("options_json", None),
            profile,
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)
        return Response(
            {"error_code": "INVALID_OPTIONS", "message": message},
            status=status.HTTP_400_BAD_REQUEST,
        )

    jobs = _matching_jobs(document, profile, options_json)

    if mode == "reuse_existing":
        existing_job = jobs.filter(
            status__in=(
                IngestionJobStatus.QUEUED,
                IngestionJobStatus.RUNNING,
                IngestionJobStatus.SUCCEEDED,
            )
        ).first()
        if existing_job:
            return Response(
                _ingest_job_payload(
                    document,
                    existing_job,
                    mode=mode,
                    created=False,
                    reused=True,
                ),
                status=status.HTTP_200_OK,
            )

    if mode == "retry_failed":
        retry_job = jobs.filter(
            status__in=(IngestionJobStatus.FAILED, IngestionJobStatus.QUARANTINED)
        ).first()
        if not retry_job:
            return Response(
                {
                    "error_code": "NOT_RETRYABLE",
                    "message": "No retryable job exists for this document and options.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if retry_job.attempt >= retry_job.max_retries:
            return _retry_job(retry_job)
        source = _copy_document_source_for_job(document)
        if not source:
            return _missing_source_response()
        retry_response = _retry_job(
            retry_job,
            source_relpath=source[0],
            source_abs_path=source[1],
        )
        if retry_response.status_code != status.HTTP_200_OK:
            return retry_response
        retry_job.refresh_from_db()
        return Response(
            _ingest_job_payload(
                document,
                retry_job,
                mode=mode,
                created=False,
                retried=True,
            ),
            status=status.HTTP_200_OK,
        )

    source = _copy_document_source_for_job(document)
    if not source:
        return _missing_source_response()
    source_relpath, source_abs_path = source
    job = _create_ingestion_job(
        api_key,
        document,
        options_json=options_json,
        profile=profile,
        source_relpath=source_relpath,
    )
    try:
        start_ingestion_pipeline(job.id)
    except Exception:
        job.delete()
        _safe_remove_file(source_abs_path)
        return _queue_unavailable_response()

    return Response(
        _ingest_job_payload(
            document,
            job,
            mode=mode,
            created=True,
        ),
        status=status.HTTP_201_CREATED,
    )


def retry_job_for_api_key(api_key, job_id) -> Response:
    if not _has_any_scope(api_key, {"jobs:write"}):
        return _scope_denied_response()
    job = IngestionJob.objects.filter(tenant=api_key.tenant, pk=job_id).first()
    if not job:
        return Response(status=status.HTTP_404_NOT_FOUND)
    if job.status not in (IngestionJobStatus.FAILED, IngestionJobStatus.QUARANTINED):
        return _retry_job(job)
    if job.attempt >= job.max_retries:
        return _retry_job(job)
    source = _copy_document_source_for_job(job.document)
    if source:
        return _retry_job(job, source_relpath=source[0], source_abs_path=source[1])
    if job.source_relpath and os.path.exists(os.path.join(settings.DATA_ROOT, job.source_relpath)):
        return _retry_job(job)
    return _missing_source_response()


def compare_document_for_api_key(api_key, document_id, data) -> Response:
    document = Document.objects.filter(tenant=api_key.tenant, pk=document_id).first()
    if not document:
        return Response(status=status.HTTP_404_NOT_FOUND)
    serializer = DocumentCompareSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    profiles = serializer.validated_data["profiles"]
    base_options = serializer.validated_data.get("options_json", None)

    source_path = document.get_quarantine_path()
    if source_path and os.path.exists(source_path):
        source_abs = source_path
    else:
        clean_path = document.get_clean_path()
        if not clean_path or not os.path.exists(clean_path):
            return Response(
                {"error_code": "MISSING_SOURCE_FILE", "message": "Document file is missing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        source_abs = clean_path

    comparison_id = uuid.uuid4()
    jobs = []
    created_jobs = []
    created_source_paths_by_job = {}
    for profile in profiles:
        try:
            resolved = resolve_effective_options(api_key, base_options, profile)
            options_json = resolved["effective_options"]
            validate_docling_options(options_json)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)
            return Response(
                {"error_code": "INVALID_OPTIONS", "message": message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        relpath = os.path.join(
            "uploads",
            "quarantine",
            str(document.tenant_id),
            f"{document.uuid}-{uuid.uuid4()}.pdf",
        )
        abs_path = os.path.join(settings.DATA_ROOT, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        shutil.copy2(source_abs, abs_path)

        job = IngestionJob.objects.create(
            tenant=api_key.tenant,
            created_by_key=api_key,
            document=document,
            external_uuid=document.external_uuid,
            profile=profile,
            comparison_id=comparison_id,
            source_relpath=relpath,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
            queued_at=timezone.now(),
            options_json=options_json or {},
        )
        created_jobs.append(job)
        created_source_paths_by_job[job.id] = abs_path

    queued_job_ids = set()
    try:
        for job in created_jobs:
            start_ingestion_pipeline(job.id)
            queued_job_ids.add(job.id)
            jobs.append({"job_id": job.id, "profile": job.profile})
    except Exception:
        rollback_jobs = [job for job in created_jobs if job.id not in queued_job_ids]
        rollback_paths = [
            created_source_paths_by_job[job.id]
            for job in rollback_jobs
            if job.id in created_source_paths_by_job
        ]
        _rollback_created_jobs(rollback_jobs, rollback_paths)
        if jobs:
            return Response(
                {
                    "error_code": "PARTIAL_QUEUE_FAILURE",
                    "message": (
                        "Some comparison jobs were queued, but not all profiles could "
                        "be submitted. Track the returned jobs before retrying."
                    ),
                    "comparison_id": str(comparison_id),
                    "document_id": document.id,
                    "jobs": jobs,
                    "failed_profiles": [job.profile for job in rollback_jobs],
                },
                status=status.HTTP_202_ACCEPTED,
            )
        return _queue_unavailable_response()

    return Response(
        {"comparison_id": str(comparison_id), "document_id": document.id, "jobs": jobs},
        status=status.HTTP_201_CREATED,
    )


def preview_artifact_for_api_key(api_key, artifact_id) -> Response:
    if not _has_any_scope(api_key, {"artifacts:read"}):
        return _scope_denied_response()
    artifact = Artifact.objects.filter(tenant=api_key.tenant, pk=artifact_id).first()
    if not artifact:
        return Response(status=status.HTTP_404_NOT_FOUND)
    abs_path = os.path.join(settings.DATA_ROOT, artifact.storage_relpath)
    if not os.path.exists(abs_path):
        return Response(status=status.HTTP_404_NOT_FOUND)

    payload = {
        "id": artifact.id,
        "kind": artifact.kind,
        "job_id": artifact.job_id,
        "size_bytes": artifact.size_bytes,
        "content_type": artifact.content_type,
        "checksum_sha256": artifact.checksum_sha256,
        "preview_limit_bytes": ARTIFACT_PREVIEW_BYTES,
        "truncated": False,
    }

    if artifact.kind == ArtifactKind.FIGURES_ZIP or artifact.content_type == "application/zip":
        try:
            with zipfile.ZipFile(abs_path, "r") as archive:
                infos = archive.infolist()
                entries = [
                    {
                        "filename": info.filename,
                        "size_bytes": info.file_size,
                        "compressed_size_bytes": info.compress_size,
                    }
                    for info in infos[:ARTIFACT_PREVIEW_ZIP_ENTRIES]
                ]
        except zipfile.BadZipFile:
            return Response(
                {"error_code": "INVALID_ZIP", "message": "Artifact is not a valid ZIP file."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload.update(
            {
                "preview_type": "zip_metadata",
                "entries": entries,
                "entry_count": len(infos),
                "entries_truncated": len(infos) > ARTIFACT_PREVIEW_ZIP_ENTRIES,
            }
        )
        return Response(payload)

    with open(abs_path, "rb") as handle:
        raw = handle.read(ARTIFACT_PREVIEW_BYTES + 1)
    truncated = len(raw) > ARTIFACT_PREVIEW_BYTES
    if truncated:
        raw = raw[:ARTIFACT_PREVIEW_BYTES]
    text = raw.decode("utf-8", errors="replace")
    payload["truncated"] = truncated

    if artifact.kind in (ArtifactKind.DOCLING_JSON, ArtifactKind.CHUNKS_JSON):
        if artifact.kind == ArtifactKind.CHUNKS_JSON:
            payload["compatibility_note"] = (
                "DocTags compatibility payload, not real chunking yet."
            )
        if not truncated:
            try:
                payload.update({"preview_type": "json", "json": json.loads(text)})
                return Response(payload)
            except json.JSONDecodeError:
                pass

    payload.update({"preview_type": "text", "text": text})
    return Response(payload)


class DocumentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [APIKeyRequired, HasScope]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            self.required_scopes = ["documents:read"]
        elif self.action in ("create", "compare", "ingest_by_uuid"):
            self.required_scopes = ["documents:write"]
        else:
            self.required_scopes = []
        return super().get_permissions()

    def get_queryset(self):
        api_key = self.request.auth
        return Document.objects.filter(tenant=api_key.tenant).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        return create_document_for_api_key(request.auth, request.data, request)

    def ingest_by_uuid(self, request, document_uuid=None):
        return ingest_document_for_api_key(request.auth, document_uuid, request.data)

    @action(detail=True, methods=["post"], url_path="compare")
    def compare(self, request, pk=None):
        self.get_object()
        return compare_document_for_api_key(request.auth, pk, request.data)


class ArtifactViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Artifact.objects.all()
    serializer_class = ArtifactSerializer
    permission_classes = [APIKeyRequired, HasScope]

    def get_permissions(self):
        self.required_scopes = ["artifacts:read"]
        return super().get_permissions()

    def get_queryset(self):
        api_key = self.request.auth
        queryset = Artifact.objects.filter(tenant=api_key.tenant).order_by("-created_at")
        job_id = self.request.query_params.get("job_id")
        if job_id:
            queryset = queryset.filter(job_id=job_id)
        return queryset

    def retrieve(self, request, *args, **kwargs):
        artifact = self.get_object()
        api_key = request.auth
        if artifact.tenant_id != api_key.tenant_id:
            return Response(status=status.HTTP_404_NOT_FOUND)

        relpath = artifact.storage_relpath
        abs_path = os.path.join(settings.DATA_ROOT, relpath)

        if not os.path.exists(abs_path):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if settings.X_ACCEL_REDIRECT_ENABLED:
            response = Response()
            response["X-Accel-Redirect"] = os.path.join(
                settings.X_ACCEL_REDIRECT_LOCATION, relpath
            )
            response["Content-Type"] = artifact.content_type or "application/octet-stream"
            response["Content-Disposition"] = f'attachment; filename="{artifact.kind}"'
            return response

        return FileResponse(
            open(abs_path, "rb"),
            as_attachment=True,
            filename=artifact.kind,
            content_type=artifact.content_type or "application/octet-stream",
        )

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        self.get_object()
        return preview_artifact_for_api_key(request.auth, pk)


class JobViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = IngestionJob.objects.all()
    serializer_class = JobSerializer
    permission_classes = [APIKeyRequired, HasScope]

    def get_permissions(self):
        if self.action in ("cancel", "retry"):
            self.required_scopes = ["jobs:write"]
        else:
            self.required_scopes = ["jobs:read"]
        return super().get_permissions()

    def get_queryset(self):
        api_key = self.request.auth
        queryset = IngestionJob.objects.filter(tenant=api_key.tenant).order_by("-created_at")

        def parse_iso(value: str):
            if not value:
                return None
            value = value.strip()
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            else:
                if " " in value:
                    base, tail = value.rsplit(" ", 1)
                    is_offset = False
                    if tail.startswith(("+", "-")) and len(tail) in (5, 6):
                        offset = tail[1:]
                        is_offset = offset.replace(":", "").isdigit()
                    elif "T" in base and len(tail) in (4, 5) and tail.replace(":", "").isdigit():
                        tail = f"+{tail}"
                        is_offset = True
                    if is_offset:
                        if len(tail) == 5:
                            tail = f"{tail[:3]}:{tail[3:]}"
                        value = f"{base}{tail}"
            dt = timezone.datetime.fromisoformat(value)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt

        external_uuid = self.request.query_params.get("external_uuid")
        if external_uuid:
            try:
                parsed = uuid.UUID(external_uuid)
            except ValueError:
                return IngestionJob.objects.none()
            queryset = queryset.filter(external_uuid=parsed)
        comparison_id = self.request.query_params.get("comparison_id")
        if comparison_id:
            try:
                parsed = uuid.UUID(comparison_id)
            except ValueError:
                return IngestionJob.objects.none()
            queryset = queryset.filter(comparison_id=parsed)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        stage_param = self.request.query_params.get("stage")
        if stage_param:
            queryset = queryset.filter(stage=stage_param)
        document_id = self.request.query_params.get("document_id")
        if document_id:
            queryset = queryset.filter(document_id=document_id)
        updated_after = self.request.query_params.get("updated_after")
        if updated_after:
            try:
                dt = parse_iso(updated_after)
                queryset = queryset.filter(modified_at__gte=dt)
            except ValueError:
                return IngestionJob.objects.none()
        created_after = self.request.query_params.get("created_after")
        if created_after:
            try:
                dt = parse_iso(created_after)
                queryset = queryset.filter(created_at__gte=dt)
            except ValueError:
                return IngestionJob.objects.none()
        created_before = self.request.query_params.get("created_before")
        if created_before:
            try:
                dt = parse_iso(created_before)
                queryset = queryset.filter(created_at__lte=dt)
            except ValueError:
                return IngestionJob.objects.none()
        return queryset

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        job = self.get_object()
        if job.status not in (IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING):
            return Response(
                {"error_code": "NOT_CANCELABLE", "message": "Job cannot be canceled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        prev_status = job.status
        prev_stage = job.stage
        job.status = IngestionJobStatus.CANCELED
        job.finished_at = timezone.now()
        job.recompute_durations()
        job.save()
        queue_job_webhooks(job, prev_status, prev_stage)
        if job.celery_task_id:
            try:
                current_app.control.revoke(
                    job.celery_task_id,
                    terminate=True,
                    signal=settings.CELERY_CANCEL_SIGNAL,
                )
            except Exception:
                pass
        return Response(JobSerializer(job).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        self.get_object()
        return retry_job_for_api_key(request.auth, pk)


class WebhookEndpointViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = WebhookEndpoint.objects.all()
    serializer_class = WebhookEndpointSerializer
    permission_classes = [APIKeyRequired, HasScope]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            self.required_scopes = ["webhooks:read"]
        else:
            self.required_scopes = ["webhooks:write"]
        return super().get_permissions()

    def get_queryset(self):
        api_key = self.request.auth
        return WebhookEndpoint.objects.filter(tenant=api_key.tenant).order_by("-created_at")

    def perform_create(self, serializer):
        events = serializer.validated_data.get("events")
        if not events:
            events = ["job.updated"]
        serializer.save(
            tenant=self.request.auth.tenant,
            created_by_key=self.request.auth,
            events=events,
        )


class DoclingProfilesView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes: list[str] = []

    def get(self, request):
        if not _has_any_scope(request.auth, DOCLING_METADATA_SCOPES):
            return _scope_denied_response()
        return Response({"profiles": profile_catalog()})


class DoclingCapabilitiesView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes: list[str] = []

    def get(self, request):
        if not _has_any_scope(request.auth, DOCLING_METADATA_SCOPES):
            return _scope_denied_response()
        return Response(capabilities_payload())


class DoclingOptionsResolveView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes: list[str] = []

    def post(self, request):
        if not _has_any_scope(request.auth, DOCLING_METADATA_SCOPES):
            return _scope_denied_response()
        serializer = DoclingOptionsResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.validated_data.get("profile") or None
        options_json = serializer.validated_data.get("options_json", None)
        try:
            resolved = resolve_effective_options(request.auth, options_json, profile)
            validate_docling_options(resolved["effective_options"])
        except ValidationError as exc:
            message = "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)
            return Response(
                {"error_code": "INVALID_OPTIONS", "message": message},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(resolved)
