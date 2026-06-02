from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .docling_options import STRUCTURED_OPTION_KEYS
from .models import Artifact, Document, IngestionJob, WebhookEndpoint
from .profiles import PROFILE_NAMES
from .validators import validate_webhook_url


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = (
            "id",
            "uuid",
            "external_uuid",
            "original_filename",
            "sha256",
            "mime_type",
            "size_bytes",
            "status",
            "page_count",
            "created_at",
        )


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    ingest = serializers.BooleanField(required=False, default=False)
    options_json = serializers.JSONField(required=False)
    external_uuid = serializers.UUIDField(required=False)
    profile = serializers.ChoiceField(choices=PROFILE_NAMES, required=False)
    duplicate_policy = serializers.ChoiceField(
        choices=("conflict", "return_existing"),
        required=False,
        default="conflict",
    )


class DocumentCompareSerializer(serializers.Serializer):
    profiles = serializers.ListField(
        child=serializers.ChoiceField(choices=PROFILE_NAMES), allow_empty=False
    )
    options_json = serializers.JSONField(required=False)


class DocumentIngestSerializer(serializers.Serializer):
    profile = serializers.ChoiceField(choices=PROFILE_NAMES, required=False)
    options_json = serializers.JSONField(required=False)
    mode = serializers.ChoiceField(
        choices=("reuse_existing", "retry_failed", "create_new"),
        required=False,
        default="reuse_existing",
    )


class DoclingOptionsResolveSerializer(serializers.Serializer):
    profile = serializers.ChoiceField(choices=PROFILE_NAMES, required=False, allow_blank=True)
    options_json = serializers.JSONField(required=False)


class ArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artifact
        fields = (
            "id",
            "kind",
            "job_id",
            "storage_relpath",
            "checksum_sha256",
            "size_bytes",
            "content_type",
            "created_at",
        )


class JobSerializer(serializers.ModelSerializer):
    error_details_json = serializers.SerializerMethodField()
    options_json = serializers.SerializerMethodField()

    class Meta:
        model = IngestionJob
        fields = (
            "id",
            "uuid",
            "document_id",
            "external_uuid",
            "profile",
            "comparison_id",
            "status",
            "stage",
            "queued_at",
            "started_at",
            "finished_at",
            "duration_ms",
            "scan_ms",
            "convert_ms",
            "export_ms",
            "chunk_ms",
            "docling_version",
            "docling_core_version",
            "docling_parse_version",
            "options_json",
            "runtime_json",
            "result_metrics_json",
            "worker_hostname",
            "celery_task_id",
            "attempt",
            "max_retries",
            "error_code",
            "error_message",
            "error_details_json",
            "created_at",
        )

    def get_error_details_json(self, obj):
        if getattr(settings, "API_INCLUDE_ERROR_DETAILS", False):
            return obj.error_details_json
        return None

    def get_options_json(self, obj):
        options = obj.options_json if isinstance(obj.options_json, dict) else {}
        sanitized = {}
        for key, value in options.items():
            if key == "ocr_options" and isinstance(value, dict):
                nested = {
                    nested_key: value[nested_key]
                    for nested_key in ("kind", "lang", "force_full_page_ocr")
                    if nested_key in value
                }
                if nested:
                    sanitized[key] = nested
            elif key in STRUCTURED_OPTION_KEYS:
                sanitized[key] = value
        return sanitized


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = (
            "id",
            "name",
            "url",
            "secret",
            "events",
            "enabled",
            "last_success_at",
            "last_failure_at",
            "created_at",
        )
        extra_kwargs = {
            "secret": {"write_only": True},
        }

    def validate_events(self, value):
        if value is None:
            return value
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("events must be a list of strings.")
        return value

    def validate_url(self, value):
        try:
            validate_webhook_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value
