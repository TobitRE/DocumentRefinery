from rest_framework import serializers

from .models import Artifact, Document, IngestionJob, WebhookEndpoint
from .profiles import PROFILE_NAMES


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
    class Meta:
        model = IngestionJob
        fields = (
            "id",
            "document_id",
            "external_uuid",
            "profile",
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
            "attempt",
            "max_retries",
            "error_code",
            "error_message",
            "error_details_json",
            "created_at",
        )


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
