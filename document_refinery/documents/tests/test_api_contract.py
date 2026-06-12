from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import (
    Artifact,
    ArtifactKind,
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    WebhookEndpoint,
)


@override_settings(WEBHOOK_ALLOWED_HOSTS=["example.com"])
class TestV1Pagination(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read", "jobs:read", "artifacts:read", "webhooks:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def _document(self, index: int) -> Document:
        return Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename=f"sample-{index}.pdf",
            sha256=f"{index:064x}"[-64:],
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine=f"uploads/quarantine/{index}/sample.pdf",
        )

    def test_all_v1_list_endpoints_are_paginated(self):
        doc = self._document(1)
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        Artifact.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            job=job,
            kind=ArtifactKind.TEXT,
            storage_relpath="artifacts/1/1/doc.txt",
            checksum_sha256="a" * 64,
            size_bytes=5,
        )
        WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Primary",
            url="https://example.com/webhook",
            secret="secret",
        )

        for path in ("/v1/documents/", "/v1/jobs/", "/v1/artifacts/", "/v1/webhooks/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
                self.assertEqual(response.data["count"], 1)
                self.assertEqual(len(response.data["results"]), 1)

    def test_default_page_size_is_50_and_page_size_is_capped_at_200(self):
        for index in range(205):
            self._document(index + 1)

        response = self.client.get("/v1/documents/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 205)
        self.assertEqual(len(response.data["results"]), 50)
        self.assertIsNotNone(response.data["next"])

        response = self.client.get("/v1/documents/?page_size=500")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 200)


class TestV1SchemaAndErrors(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=[],
            active=True,
        )

    def test_schema_requires_authentication(self):
        response = self.client.get("/v1/schema/", HTTP_X_REQUEST_ID="schema-denied")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error_code"], "AUTHENTICATION_REQUIRED")
        self.assertEqual(response.data["request_id"], "schema-denied")

    def test_schema_is_accessible_with_api_key(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        response = self.client.get("/v1/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["openapi"][:3], "3.0")
        self.assertIn("/v1/documents/", response.data["paths"])
        upload_schema_ref = response.data["paths"]["/v1/documents/"]["post"][
            "requestBody"
        ]["content"]["multipart/form-data"]["schema"]["$ref"]
        self.assertEqual(upload_schema_ref, "#/components/schemas/DocumentUploadRequest")

    def test_schema_is_accessible_to_staff_session(self):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get("/v1/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/v1/jobs/", response.data["paths"])

    def test_error_format_includes_request_id(self):
        response = self.client.get("/v1/documents/", HTTP_X_REQUEST_ID="req-123")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(set(response.data), {"error_code", "message", "request_id"})
        self.assertEqual(response.data["request_id"], "req-123")

    def test_manual_error_response_includes_request_id(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        response = self.client.get("/v1/docling/profiles/", HTTP_X_REQUEST_ID="manual-403")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error_code"], "INSUFFICIENT_SCOPE")
        self.assertEqual(response.data["request_id"], "manual-403")
