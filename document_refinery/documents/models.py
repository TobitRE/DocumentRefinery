import os

from django.conf import settings
from django.db import models
from django.utils import timezone

from authn.models import APIKey, Tenant
from core.models import BaseModel


class DocumentStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    CLEAN = "CLEAN", "Clean"
    INFECTED = "INFECTED", "Infected"
    DELETED = "DELETED", "Deleted"


class IngestionJobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    CANCELED = "CANCELED", "Canceled"
    QUARANTINED = "QUARANTINED", "Quarantined"


class IngestionStage(models.TextChoices):
    SCANNING = "SCANNING", "Scanning"
    CONVERTING = "CONVERTING", "Converting"
    EXPORTING = "EXPORTING", "Exporting"
    CHUNKING = "CHUNKING", "Chunking"
    FINALIZING = "FINALIZING", "Finalizing"


class ArtifactKind(models.TextChoices):
    DOCLING_JSON = "docling_json", "Docling JSON"
    MARKDOWN = "markdown", "Markdown"
    TEXT = "text", "Text"
    DOCTAGS = "doctags", "DocTags"
    CHUNKS_JSON = "chunks_json", "Chunks JSON"
    FIGURES_ZIP = "figures_zip", "Figures ZIP"


class Document(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="documents")
    created_by_key = models.ForeignKey(
        APIKey, on_delete=models.PROTECT, related_name="documents_created"
    )
    external_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, blank=True, null=True)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    storage_relpath_quarantine = models.CharField(max_length=500)
    storage_relpath_clean = models.CharField(max_length=500, blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.UPLOADED
    )
    page_count = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "sha256"],
                name="uniq_document_sha256_per_tenant",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.tenant_id})"

    def get_quarantine_path(self) -> str:
        return os.path.join(settings.DATA_ROOT, self.storage_relpath_quarantine)

    def get_clean_path(self) -> str:
        if not self.storage_relpath_clean:
            return ""
        return os.path.join(settings.DATA_ROOT, self.storage_relpath_clean)


class IngestionJob(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="jobs")
    created_by_key = models.ForeignKey(
        APIKey, on_delete=models.PROTECT, related_name="jobs_created"
    )
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="jobs")
    external_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    profile = models.CharField(max_length=50, blank=True, null=True)
    comparison_id = models.UUIDField(null=True, blank=True, db_index=True)
    source_relpath = models.CharField(max_length=500, blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=IngestionJobStatus.choices,
        default=IngestionJobStatus.QUEUED,
    )
    stage = models.CharField(
        max_length=20, choices=IngestionStage.choices, default=IngestionStage.SCANNING
    )
    options_json = models.JSONField(default=dict, blank=True)
    docling_version = models.CharField(max_length=50, blank=True)

    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    scan_ms = models.PositiveIntegerField(null=True, blank=True)
    convert_ms = models.PositiveIntegerField(null=True, blank=True)
    export_ms = models.PositiveIntegerField(null=True, blank=True)
    chunk_ms = models.PositiveIntegerField(null=True, blank=True)

    attempt = models.PositiveSmallIntegerField(default=0)
    max_retries = models.PositiveSmallIntegerField(default=3)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    error_details_json = models.JSONField(null=True, blank=True)

    worker_hostname = models.CharField(max_length=255, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "stage"]),
            models.Index(fields=["document"]),
            models.Index(fields=["comparison_id"]),
        ]

    def __str__(self) -> str:
        return f"Job {self.id} ({self.status})"

    def mark_started(self) -> None:
        if not self.started_at:
            self.started_at = timezone.now()
        self.status = IngestionJobStatus.RUNNING
        self.save()

    def mark_finished(self, status: str) -> None:
        if not self.finished_at:
            self.finished_at = timezone.now()
        self.status = status
        self.recompute_durations()
        self.save()

    def recompute_durations(self) -> None:
        if self.started_at and self.finished_at:
            delta = self.finished_at - self.started_at
            self.duration_ms = int(delta.total_seconds() * 1000)


class Artifact(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="artifacts")
    created_by_key = models.ForeignKey(
        APIKey, on_delete=models.PROTECT, related_name="artifacts_created"
    )
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="artifacts")
    kind = models.CharField(max_length=30, choices=ArtifactKind.choices)
    storage_relpath = models.CharField(max_length=500)
    checksum_sha256 = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()
    content_type = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "job", "kind"],
                name="uniq_artifact_kind_per_job",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "job"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.job_id})"

    def get_storage_path(self) -> str:
        return os.path.join(settings.DATA_ROOT, self.storage_relpath)


class JobEvent(BaseModel):
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="events")
    level = models.CharField(max_length=10, default="INFO")
    message = models.TextField()
    payload_json = models.JSONField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.level}: {self.message[:40]}"


class WebhookDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RETRYING = "RETRYING", "Retrying"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"


class WebhookEndpoint(BaseModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="webhooks")
    created_by_key = models.ForeignKey(
        APIKey, on_delete=models.PROTECT, related_name="webhooks_created"
    )
    name = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)
    events = models.JSONField(default=list, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "enabled"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.tenant_id})"


class WebhookDelivery(BaseModel):
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_type = models.CharField(max_length=100)
    payload_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=WebhookDeliveryStatus.choices,
        default=WebhookDeliveryStatus.PENDING,
    )
    response_code = models.PositiveSmallIntegerField(null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["endpoint", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.endpoint_id} ({self.status})"
