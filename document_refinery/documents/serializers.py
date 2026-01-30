from rest_framework import serializers

from .models import Artifact, Document


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
