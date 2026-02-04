from django.test import TestCase, override_settings
from django.db.utils import OperationalError
from rest_framework.test import APIClient
from unittest.mock import MagicMock, patch

from authn.models import APIKey, Tenant
from documents.models import Document, IngestionJob, IngestionJobStatus


class TestInternalTokenGuard(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_healthz_requires_token_when_configured(self):
        with override_settings(INTERNAL_ENDPOINTS_TOKEN="secret-token"):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 403)

            response = self.client.get("/healthz?token=secret-token")
            self.assertEqual(response.status_code, 403)

            response = self.client.get("/healthz", HTTP_X_INTERNAL_TOKEN="secret-token")
            self.assertEqual(response.status_code, 200)
            self.assertIn("docling_version", response.json())

    def test_healthz_denies_without_configured_token(self):
        with override_settings(INTERNAL_ENDPOINTS_TOKEN=""):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 403)


class TestCoreViews(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=[],
            active=True,
        )
        self.doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )

    @override_settings(INTERNAL_ENDPOINTS_TOKEN="secret")
    def test_readyz_ok_and_degraded(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = None
        mock_broker = MagicMock()
        mock_broker.ensure_connection.return_value = None

        with patch("core.views.connections", {"default": mock_conn}), patch(
            "core.views.current_app.connection", return_value=mock_broker
        ):
            response = self.client.get("/readyz", HTTP_X_INTERNAL_TOKEN="secret")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "ok")

        mock_conn.cursor.side_effect = OperationalError("db down")
        with patch("core.views.connections", {"default": mock_conn}), patch(
            "core.views.current_app.connection", return_value=mock_broker
        ):
            response = self.client.get("/readyz", HTTP_X_INTERNAL_TOKEN="secret")
            self.assertEqual(response.status_code, 503)
            payload = response.json()
            self.assertEqual(payload["status"], "degraded")

    @override_settings(INTERNAL_ENDPOINTS_TOKEN="secret")
    def test_metrics(self):
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
        )
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.RUNNING,
        )
        response = self.client.get("/metrics", HTTP_X_INTERNAL_TOKEN="secret")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        self.assertIn('docling_jobs_total{status="queued"}', text)
        self.assertIn('docling_jobs_total{status="running"}', text)
