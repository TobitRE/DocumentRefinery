from django.test import RequestFactory, TestCase

from authn.models import APIKey, Tenant
from authn.throttling import APIKeyRateThrottle


class TestAPIKeyRateThrottle(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read"],
            active=True,
        )

    def test_uses_request_api_key_hash(self):
        request = self.factory.get("/v1/documents/")
        request.api_key = self.api_key

        cache_key = APIKeyRateThrottle().get_cache_key(request, view=None)

        self.assertEqual(cache_key, f"api_key:{self.api_key.key_hash}")

    def test_uses_request_auth_api_key_hash(self):
        request = self.factory.get("/v1/documents/")
        request.auth = self.api_key

        cache_key = APIKeyRateThrottle().get_cache_key(request, view=None)

        self.assertEqual(cache_key, f"api_key:{self.api_key.key_hash}")

    def test_anonymous_requests_use_client_ident(self):
        request = self.factory.get("/v1/documents/", REMOTE_ADDR="203.0.113.10")

        cache_key = APIKeyRateThrottle().get_cache_key(request, view=None)

        self.assertEqual(cache_key, "api_key:anon:203.0.113.10")

    def test_anonymous_requests_without_ident_are_not_throttled_by_this_class(self):
        request = self.factory.get("/v1/documents/")
        throttle = APIKeyRateThrottle()
        throttle.get_ident = lambda request: ""

        self.assertIsNone(throttle.get_cache_key(request, view=None))
