from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from authn.models import APIKey, Tenant
from authn.permissions import APIKeyRequired, HasScope


class DummyView:
    required_scopes = ["documents:read"]


class TestPermissions(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
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

    def test_api_key_required(self):
        request = self.factory.get("/")
        request = Request(request)
        request.auth = None
        self.assertFalse(APIKeyRequired().has_permission(request, DummyView()))
        request.auth = self.api_key
        self.assertTrue(APIKeyRequired().has_permission(request, DummyView()))

    def test_has_scope(self):
        request = self.factory.get("/")
        request = Request(request)
        request.auth = self.api_key
        self.assertTrue(HasScope().has_permission(request, DummyView()))

        request.auth.scopes = []
        self.assertFalse(HasScope().has_permission(request, DummyView()))
