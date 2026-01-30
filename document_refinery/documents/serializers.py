from rest_framework import serializers

from .models import Document


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
