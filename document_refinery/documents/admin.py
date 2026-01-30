from django.contrib import admin

from .models import Artifact, Document, IngestionJob


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "original_filename", "status", "size_bytes", "created_at")
    list_filter = ("tenant", "status")
    search_fields = ("original_filename", "sha256")


@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "document",
        "status",
        "stage",
        "duration_ms",
        "started_at",
        "finished_at",
    )
    list_filter = ("tenant", "status", "stage")
    readonly_fields = (
        "queued_at",
        "started_at",
        "finished_at",
        "duration_ms",
        "scan_ms",
        "convert_ms",
        "export_ms",
        "chunk_ms",
        "error_code",
        "error_message",
        "error_details_json",
        "celery_task_id",
        "worker_hostname",
    )


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "job", "size_bytes", "created_at")
    list_filter = ("kind",)

# Register your models here.
