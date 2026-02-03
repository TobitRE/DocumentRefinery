import uuid
from unittest.mock import patch

from django.test import TestCase

from authn.models import APIKey, Tenant
from documents.models import (
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    WebhookDelivery,
    WebhookEndpoint,
)
from documents.tasks import queue_job_webhooks


class TestWebhookQueue(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["jobs:read"],
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
        self.job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )

    def test_queue_creates_delivery(self):
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Primary",
            url="https://example.com/webhook",
            secret="",
            events=["job.updated"],
            enabled=True,
        )
        external_uuid = uuid.uuid4()
        self.job.status = IngestionJobStatus.RUNNING
        self.job.stage = IngestionStage.CONVERTING
        self.job.external_uuid = external_uuid
        self.job.profile = "fast_text"
        self.job.save()

        with patch("documents.tasks.deliver_webhook_delivery.delay") as delay:
            count = queue_job_webhooks(self.job, IngestionJobStatus.QUEUED, IngestionStage.SCANNING)

        self.assertEqual(count, 1)
        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        self.assertEqual(delivery.payload_json.get("job_id"), self.job.id)
        self.assertEqual(delivery.payload_json.get("external_uuid"), str(external_uuid))
        self.assertEqual(delivery.payload_json.get("profile"), "fast_text")
        delay.assert_called_once_with(delivery.id)

    def test_queue_skips_when_no_change(self):
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Primary",
            url="https://example.com/webhook",
            secret="",
            events=["job.updated"],
            enabled=True,
        )
        with patch("documents.tasks.deliver_webhook_delivery.delay") as delay:
            count = queue_job_webhooks(self.job, self.job.status, self.job.stage)

        self.assertEqual(count, 0)
        self.assertFalse(WebhookDelivery.objects.filter(endpoint=endpoint).exists())
        delay.assert_not_called()

    def test_queue_skips_unsubscribed(self):
        WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Primary",
            url="https://example.com/webhook",
            secret="",
            events=["job.failed"],
            enabled=True,
        )
        with patch("documents.tasks.deliver_webhook_delivery.delay"):
            count = queue_job_webhooks(self.job, IngestionJobStatus.RUNNING, IngestionStage.CONVERTING)

        self.assertEqual(count, 0)
        self.assertEqual(WebhookDelivery.objects.count(), 0)
