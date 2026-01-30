import hashlib
import os

from django.conf import settings
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from authn.permissions import HasScope
from authn.permissions import APIKeyRequired

from .models import Document, IngestionJob, IngestionJobStatus, IngestionStage
from .serializers import DocumentSerializer, DocumentUploadSerializer


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
            job = IngestionJob.objects.create(
                tenant=api_key.tenant,
                created_by_key=api_key,
                document=doc,
                status=IngestionJobStatus.QUEUED,
                stage=IngestionStage.SCANNING,
                options_json=options_json or {},
            )
            job_id = job.id

        payload = DocumentSerializer(doc).data
        if job_id:
            payload["job_id"] = job_id
        return Response(payload, status=status.HTTP_201_CREATED)

# Create your views here.
