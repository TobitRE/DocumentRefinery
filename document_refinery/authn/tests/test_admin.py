from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from authn.admin import APIKeyAdmin
from authn.models import APIKey, Tenant


class TestAPIKeyAdmin(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")

    def _build_request(self):
        request = self.factory.post("/admin/authn/apikey/add/")
        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def test_save_model_does_not_store_raw_key_on_admin_instance(self):
        request = self._build_request()
        admin_instance = APIKeyAdmin(APIKey, AdminSite())
        api_key = APIKey(
            tenant=self.tenant,
            name="Primary",
            scopes=["documents:read"],
            active=True,
        )

        admin_instance.save_model(request, api_key, form=None, change=False)

        self.assertFalse(hasattr(admin_instance, "_raw_key"))
        created = APIKey.objects.get(name="Primary")
        self.assertTrue(created.prefix)
        self.assertTrue(created.key_hash)

        messages_text = [str(message) for message in get_messages(request)]
        self.assertTrue(any("New API key created." in message for message in messages_text))
