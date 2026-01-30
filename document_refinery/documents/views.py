import hashlib
import os

from django.conf import settings
from django.utils import timezone
from django.http import FileResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from authn.permissions import HasScope
from authn.permissions import APIKeyRequired

from .models import Artifact, Document, IngestionJob, IngestionJobStatus, IngestionStage
from .tasks import start_ingestion_pipeline
from .serializers import (
    ArtifactSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    JobSerializer,
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
        elif self.action in ("create",):
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
        doc.save()

        job_id = None
        if ingest:
            options_json = options_json or api_key.docling_options_json or {}
            job = IngestionJob.objects.create(
                tenant=api_key.tenant,
                created_by_key=api_key,
                document=doc,
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

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        stage_param = self.request.query_params.get("stage")
        if stage_param:
            queryset = queryset.filter(stage=stage_param)
        document_id = self.request.query_params.get("document_id")
        if document_id:
            queryset = queryset.filter(document_id=document_id)
        created_after = self.request.query_params.get("created_after")
        if created_after:
            queryset = queryset.filter(created_at__gte=created_after)
        created_before = self.request.query_params.get("created_before")
        if created_before:
            queryset = queryset.filter(created_at__lte=created_before)
        return queryset

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        job = self.get_object()
        if job.status not in (IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING):
            return Response(
                {"error_code": "NOT_CANCELABLE", "message": "Job cannot be canceled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job.status = IngestionJobStatus.CANCELED
        job.finished_at = timezone.now()
        job.recompute_durations()
        job.save()
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

        start_ingestion_pipeline(job.id)
        return Response(JobSerializer(job).data)
