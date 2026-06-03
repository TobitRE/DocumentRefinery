from django.contrib import admin

from .models import DashboardActionAudit


@admin.register(DashboardActionAudit)
class DashboardActionAuditAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "action",
        "potentially_billable",
        "tenant_name",
        "api_key_name",
        "created_by_user",
        "document",
        "job",
        "created_at",
    )
    list_filter = ("action", "potentially_billable", "tenant", "created_by_user")
    search_fields = (
        "tenant_name",
        "api_key_name",
        "api_key_prefix",
        "created_by_user__username",
        "document__original_filename",
    )
    readonly_fields = (
        "uuid",
        "tenant",
        "api_key",
        "created_by_user",
        "document",
        "job",
        "artifact",
        "action",
        "potentially_billable",
        "tenant_name",
        "api_key_name",
        "api_key_prefix",
        "request_meta_json",
        "details_json",
        "created_at",
        "modified_at",
    )
