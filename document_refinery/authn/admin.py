from django.contrib import admin, messages

from .models import APIKey, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "slug")


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "prefix", "active", "created_at", "last_used_at")
    list_filter = ("active", "tenant")
    search_fields = ("name", "prefix")
    readonly_fields = ("prefix", "key_hash", "created_at", "modified_at", "last_used_at")

    def save_model(self, request, obj, form, change):
        if not change and not obj.key_hash:
            raw_key, prefix, key_hash = APIKey.generate_key()
            obj.prefix = prefix
            obj.key_hash = key_hash
            obj.save()
            self._raw_key = raw_key
            return
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue=post_url_continue)
        raw_key = getattr(self, "_raw_key", None)
        if raw_key:
            messages.warning(
                request,
                (
                    "New API key created. Copy it now; it will not be shown again: "
                    f"{raw_key}"
                ),
            )
            delattr(self, "_raw_key")
        return response

# Register your models here.
