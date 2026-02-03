import hashlib
import os
import shutil
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import FileResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from celery import current_app

from authn.permissions import HasScope
from authn.permissions import APIKeyRequired
from authn.options import validate_docling_options

from django.db import IntegrityError

from .models import (
    Artifact,
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    WebhookEndpoint,
)
from .tasks import queue_job_webhooks, start_ingestion_pipeline
from .profiles import apply_profile_to_options
from .serializers import (
    ArtifactSerializer,
    DocumentSerializer,
    DocumentCompareSerializer,
    DocumentUploadSerializer,
    JobSerializer,
    WebhookEndpointSerializer,
)


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
        elif self.action in ("create", "compare"):
            self.required_scopes = ["documents:write"]
        else:
            self.required_scopes = []
        return super().get_permissions()

    def get_queryset(self):
        api_key = self.request.auth
        return Document.objects.filter(tenant=api_key.tenant).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded = serializer.validated_data["file"]
        ingest = serializer.validated_data.get("ingest", False)
        options_json = serializer.validated_data.get("options_json", None)
        external_uuid = serializer.validated_data.get("external_uuid", None)
        profile = serializer.validated_data.get("profile", None)

        if uploaded.content_type not in ("application/pdf", "application/x-pdf"):
            return Response(
                {"error_code": "UNSUPPORTED_MEDIA_TYPE", "message": "Only PDF files are allowed."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        max_bytes = settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024
        if uploaded.size and uploaded.size > max_bytes:
            return Response(
                {"error_code": "FILE_TOO_LARGE", "message": "File exceeds size limit."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        api_key = request.auth
        tenant_id = api_key.tenant_id
        filename = uploaded.name

        doc = Document(
            tenant=api_key.tenant,
            created_by_key=api_key,
            external_uuid=external_uuid,
            original_filename=filename,
            mime_type=uploaded.content_type or "application/pdf",
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
        if Document.objects.filter(tenant=api_key.tenant, sha256=doc.sha256).exists():
            if os.path.exists(abs_path):
                os.remove(abs_path)
            return Response(
                {"error_code": "DUPLICATE_DOCUMENT", "message": "Document already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            doc.save()
        except IntegrityError:
            if os.path.exists(abs_path):
                os.remove(abs_path)
            return Response(
                {"error_code": "DUPLICATE_DOCUMENT", "message": "Document already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        job_id = None
        if ingest:
            options_json = (
                options_json
                or api_key.docling_options_json
                or getattr(api_key.tenant, "docling_options_json", None)
                or settings.DOC_DEFAULT_OPTIONS
                or {}
            )
            options_json = apply_profile_to_options(options_json, profile)
            try:
                validate_docling_options(options_json)
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
            job = IngestionJob.objects.create(
                tenant=api_key.tenant,
                created_by_key=api_key,
                document=doc,
                external_uuid=doc.external_uuid,
                profile=profile,
                status=IngestionJobStatus.QUEUED,
                stage=IngestionStage.SCANNING,
                queued_at=timezone.now(),
                options_json=options_json or {},
            )
            job_id = job.id
            start_ingestion_pipeline(job_id)

        payload = DocumentSerializer(doc).data
        if job_id:
            payload["job_id"] = job_id
        return Response(payload, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="compare")
    def compare(self, request, pk=None):
        document = self.get_object()
        serializer = DocumentCompareSerializer(data=request.data)
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
        for profile in profiles:
            options_json = (
                base_options
                or request.auth.docling_options_json
                or getattr(request.auth.tenant, "docling_options_json", None)
                or settings.DOC_DEFAULT_OPTIONS
                or {}
            )
            options_json = apply_profile_to_options(options_json, profile)
            try:
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
                tenant=request.auth.tenant,
                created_by_key=request.auth,
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
            start_ingestion_pipeline(job.id)
            jobs.append({"job_id": job.id, "profile": profile})

        return Response(
            {"comparison_id": str(comparison_id), "document_id": document.id, "jobs": jobs},
            status=status.HTTP_201_CREATED,
        )


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


class JobViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = IngestionJob.objects.all()
    serializer_class = JobSerializer
    permission_classes = [APIKeyRequired, HasScope]

    def get_permissions(self):
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
        job = self.get_object()
        if job.status not in (IngestionJobStatus.FAILED, IngestionJobStatus.QUARANTINED):
            return Response(
                {"error_code": "NOT_RETRYABLE", "message": "Job cannot be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.attempt >= job.max_retries:
            return Response(
                {"error_code": "RETRY_LIMIT", "message": "Retry limit reached."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artifacts = Artifact.objects.filter(job=job)
        for artifact in artifacts:
            abs_path = os.path.join(settings.DATA_ROOT, artifact.storage_relpath)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError:
                pass
        artifacts.delete()

        prev_status = job.status
        prev_stage = job.stage
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
        job.save()
        queue_job_webhooks(job, prev_status, prev_stage)

        start_ingestion_pipeline(job.id)
        return Response(JobSerializer(job).data)


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
