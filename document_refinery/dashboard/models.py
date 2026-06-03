from django.conf import settings
from django.db import models

from core.models import BaseModel


class DashboardActionAudit(BaseModel):
    class Action(models.TextChoices):
        DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD", "Document upload"
        DOCUMENT_DUPLICATE_REUSE = "DOCUMENT_DUPLICATE_REUSE", "Document duplicate reuse"
        DOCUMENT_INGEST = "DOCUMENT_INGEST", "Document ingest"
        JOB_RETRY = "JOB_RETRY", "Job retry"
        DOCUMENT_COMPARE = "DOCUMENT_COMPARE", "Document compare"
        ARTIFACT_PREVIEW = "ARTIFACT_PREVIEW", "Artifact preview"

    tenant = models.ForeignKey(
        "authn.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    api_key = models.ForeignKey(
        "authn.APIKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    job = models.ForeignKey(
        "documents.IngestionJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    artifact = models.ForeignKey(
        "documents.Artifact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dashboard_action_audits",
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    potentially_billable = models.BooleanField(default=False)
    tenant_name = models.CharField(max_length=200, blank=True)
    api_key_name = models.CharField(max_length=200, blank=True)
    api_key_prefix = models.CharField(max_length=8, blank=True)
    request_meta_json = models.JSONField(default=dict, blank=True)
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "action", "created_at"]),
            models.Index(fields=["created_by_user", "created_at"]),
            models.Index(fields=["potentially_billable", "created_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.action} by {self.created_by_user_id or '-'}"
