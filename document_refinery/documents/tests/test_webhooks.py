import hashlib
import hmac
import urllib.error
from unittest.mock import patch

from django.test import TestCase, override_settings

from authn.models import APIKey, Tenant
from documents.models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)
from documents.tasks import deliver_webhook_delivery


class TestWebhookDelivery(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["webhooks:write"],
            active=True,
        )

    def test_deliver_success_signs_payload(self):
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Test",
            url="https://example.com/webhook",
            secret="secret",
            events=["job.updated"],
        )
        payload = {"event": "job.updated", "job_id": 123}
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json=payload,
        )

        def fake_urlopen(request, timeout=10):
            headers = {k.lower(): v for k, v in request.header_items()}
            body = request.data
            expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            assert headers.get("x-docrefinery-signature") == f"sha256={expected}"

            class Response:
                def getcode(self):
                    return 200

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return Response()

        with patch("documents.tasks.urllib.request.urlopen", side_effect=fake_urlopen):
            deliver_webhook_delivery.apply(args=[delivery.id])

        delivery.refresh_from_db()
        endpoint.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDeliveryStatus.DELIVERED)
        self.assertEqual(delivery.response_code, 200)
        self.assertIsNotNone(delivery.delivered_at)
        self.assertIsNotNone(endpoint.last_success_at)

    @override_settings(WEBHOOK_MAX_ATTEMPTS=3, WEBHOOK_INITIAL_BACKOFF_SECONDS=1)
    def test_deliver_retry_on_failure(self):
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Test",
            url="https://example.com/webhook",
            secret="",
            events=["job.updated"],
        )
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json={"event": "job.updated"},
        )

        with patch("documents.tasks.deliver_webhook_delivery.apply_async") as apply_async:
            with patch(
                "documents.tasks.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                deliver_webhook_delivery.apply(args=[delivery.id])

        delivery.refresh_from_db()
        endpoint.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDeliveryStatus.RETRYING)
        self.assertEqual(delivery.attempt, 1)
        self.assertIsNotNone(delivery.next_retry_at)
        self.assertIsNotNone(endpoint.last_failure_at)
        apply_async.assert_called_once()

    @override_settings(WEBHOOK_MAX_ATTEMPTS=1)
    def test_deliver_marks_failed_after_max_attempts(self):
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Test",
            url="https://example.com/webhook",
            secret="",
            events=["job.updated"],
        )
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json={"event": "job.updated"},
        )

        with patch(
            "documents.tasks.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            deliver_webhook_delivery.apply(args=[delivery.id])

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDeliveryStatus.FAILED)
        self.assertIsNone(delivery.next_retry_at)
