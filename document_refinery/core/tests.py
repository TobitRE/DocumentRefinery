from django.test import TestCase, override_settings
from rest_framework.test import APIClient


class TestInternalTokenGuard(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_healthz_requires_token_when_configured(self):
        with override_settings(INTERNAL_ENDPOINTS_TOKEN="secret-token"):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 403)

            response = self.client.get("/healthz", HTTP_X_INTERNAL_TOKEN="secret-token")
            self.assertEqual(response.status_code, 200)
            self.assertIn("docling_version", response.json())

    def test_healthz_open_without_token(self):
        with override_settings(INTERNAL_ENDPOINTS_TOKEN=""):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertIn("docling_version", response.json())
