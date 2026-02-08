from django.contrib import admin, messages

from .models import APIKey, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "slug")
    fieldsets = (
        (None, {"fields": ("name", "slug", "active")}),
        ("Docling defaults", {"fields": ("docling_options_json",)}),
    )


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "prefix", "active", "created_at", "last_used_at")
    list_filter = ("active", "tenant")
    search_fields = ("name", "prefix")
    readonly_fields = ("prefix", "key_hash", "created_at", "modified_at", "last_used_at")
    actions = ("deactivate_keys", "rotate_keys")
    fieldsets = (
        (None, {"fields": ("tenant", "name", "active", "scopes", "allowed_upload_mime_types")}),
        ("Docling defaults", {"fields": ("docling_options_json",)}),
        ("Key data", {"fields": ("prefix", "key_hash", "created_at", "modified_at", "last_used_at")}),
    )

    def save_model(self, request, obj, form, change):
        raw_key = None
        if not change and not obj.key_hash:
            raw_key, prefix, key_hash = APIKey.generate_key()
            obj.prefix = prefix
            obj.key_hash = key_hash
        super().save_model(request, obj, form, change)
        if raw_key:
            messages.warning(
                request,
                (
                    "New API key created. Copy it now; it will not be shown again: "
                    f"{raw_key}"
                ),
            )

    @admin.action(description="Deactivate selected API keys")
    def deactivate_keys(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(request, f"Deactivated {updated} keys.")

    @admin.action(description="Rotate selected API keys (new secret shown once)")
    def rotate_keys(self, request, queryset):
        rotated = 0
        for api_key in queryset:
            raw_key, prefix, key_hash = APIKey.generate_key()
            api_key.prefix = prefix
            api_key.key_hash = key_hash
            api_key.active = True
            api_key.save()
            self.message_user(
                request,
                f"{api_key.name} rotated key (copy now): {raw_key}",
                messages.WARNING,
            )
            rotated += 1
        self.message_user(request, f"Rotated {rotated} keys.")

# Register your models here.
