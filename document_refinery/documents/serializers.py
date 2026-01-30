from rest_framework import serializers

from .models import Artifact, Document, IngestionJob


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = (
            "id",
            "uuid",
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
