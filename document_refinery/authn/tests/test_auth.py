from django.test import TestCase
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant


class TestAPIKeyAuth(TestCase):
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
            scopes=["documents:read"],
            active=True,
        )

    def test_missing_key_rejected(self):
        response = self.client.get("/v1/documents/")
        self.assertEqual(response.status_code, 401)

    def test_invalid_key_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Api-Key invalid")
        response = self.client.get("/v1/documents/")
        self.assertEqual(response.status_code, 401)

    def test_valid_key_allows_access(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        response = self.client.get("/v1/documents/")
        self.assertEqual(response.status_code, 200)
