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

    def test_deactivate_keys_marks_selected_keys_inactive(self):
        request = self._build_request()
        admin_instance = APIKeyAdmin(APIKey, AdminSite())
        raw_key, prefix, key_hash = APIKey.generate_key()
        api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read"],
            active=True,
        )

        admin_instance.deactivate_keys(request, APIKey.objects.filter(pk=api_key.pk))

        api_key.refresh_from_db()
        self.assertFalse(api_key.active)
        messages_text = [str(message) for message in get_messages(request)]
        self.assertTrue(any("Deactivated 1 keys." in message for message in messages_text))

    def test_rotate_keys_replaces_secret_and_reactivates_key(self):
        request = self._build_request()
        admin_instance = APIKeyAdmin(APIKey, AdminSite())
        old_raw_key, prefix, key_hash = APIKey.generate_key()
        api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read"],
            active=False,
        )
        old_prefix = api_key.prefix
        old_hash = api_key.key_hash

        admin_instance.rotate_keys(request, APIKey.objects.filter(pk=api_key.pk))

        api_key.refresh_from_db()
        self.assertTrue(api_key.active)
        self.assertNotEqual(api_key.prefix, old_prefix)
        self.assertNotEqual(api_key.key_hash, old_hash)
        self.assertIsNone(APIKey.lookup_from_raw(old_raw_key))
        messages_text = [str(message) for message in get_messages(request)]
        self.assertTrue(any("rotated key" in message for message in messages_text))
        self.assertTrue(any("Rotated 1 keys." in message for message in messages_text))
