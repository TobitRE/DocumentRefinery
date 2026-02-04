from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import WebhookEndpoint


@override_settings(WEBHOOK_ALLOWED_HOSTS=["example.com"])
class TestWebhookEndpointAPI(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Beta", slug="beta")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["webhooks:read", "webhooks:write"],
            active=True,
        )
        raw_key_ro, prefix_ro, key_hash_ro = APIKey.generate_key()
        self.raw_key_ro = raw_key_ro
        self.api_key_ro = APIKey.objects.create(
            tenant=self.tenant,
            name="ReadOnly",
            prefix=prefix_ro,
            key_hash=key_hash_ro,
            scopes=["webhooks:read"],
            active=True,
        )
        raw_key_other, prefix_other, key_hash_other = APIKey.generate_key()
        self.raw_key_other = raw_key_other
        self.api_key_other = APIKey.objects.create(
            tenant=self.other_tenant,
            name="Other",
            prefix=prefix_other,
            key_hash=key_hash_other,
            scopes=["webhooks:read", "webhooks:write"],
            active=True,
        )
        raw_key_write, prefix_write, key_hash_write = APIKey.generate_key()
        self.raw_key_write = raw_key_write
        APIKey.objects.create(
            tenant=self.tenant,
            name="WriteOnly",
            prefix=prefix_write,
            key_hash=key_hash_write,
            scopes=["webhooks:write"],
            active=True,
        )

    def _auth(self, raw_key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")

    def test_webhook_crud(self):
        self._auth(self.raw_key)
        payload = {
            "name": "Primary",
            "url": "https://example.com/webhook",
            "secret": "supersecret",
            "events": ["job.updated"],
            "enabled": True,
        }
        response = self.client.post("/v1/webhooks/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        endpoint_id = response.data["id"]
        self.assertNotIn("secret", response.data)

        endpoint = WebhookEndpoint.objects.get(pk=endpoint_id)
        self.assertEqual(endpoint.created_by_key_id, self.api_key.id)
        self.assertEqual(endpoint.tenant_id, self.tenant.id)

        response = self.client.get("/v1/webhooks/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data}
        self.assertIn(endpoint_id, ids)

        response = self.client.get(f"/v1/webhooks/{endpoint_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Primary")

        response = self.client.patch(
            f"/v1/webhooks/{endpoint_id}/",
            {"enabled": False, "events": ["job.updated", "job.failed"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        endpoint.refresh_from_db()
        self.assertFalse(endpoint.enabled)
        self.assertIn("job.failed", endpoint.events)

        response = self.client.delete(f"/v1/webhooks/{endpoint_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(WebhookEndpoint.objects.filter(pk=endpoint_id).exists())

    def test_webhook_scope_enforcement(self):
        self._auth(self.raw_key_ro)
        response = self.client.post(
            "/v1/webhooks/",
            {"name": "Blocked", "url": "https://example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        self._auth(self.raw_key_write)
        response = self.client.get("/v1/webhooks/")
        self.assertEqual(response.status_code, 403)

    def test_webhook_tenant_scoping(self):
        self._auth(self.raw_key)
        response = self.client.post(
            "/v1/webhooks/",
            {"name": "Primary", "url": "https://example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        self._auth(self.raw_key_other)
        response = self.client.get("/v1/webhooks/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_webhook_rejects_private_host(self):
        self._auth(self.raw_key)
        response = self.client.post(
            "/v1/webhooks/",
            {"name": "Private", "url": "http://127.0.0.1:8000"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("url", response.data)
