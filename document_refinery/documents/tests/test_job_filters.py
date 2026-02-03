import uuid
from datetime import timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import Document, IngestionJob, IngestionJobStatus, IngestionStage


class TestJobFilters(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["jobs:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

        self.doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )

    def test_updated_after_filters_jobs(self):
        job_old = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        job_new = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )

        since = timezone.now()
        job_new.status = IngestionJobStatus.RUNNING
        job_new.save()

        response = self.client.get(f"/v1/jobs/?updated_after={since.isoformat()}")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data}
        self.assertIn(job_new.id, ids)
        self.assertNotIn(job_old.id, ids)

    def test_updated_after_space_separator(self):
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        since = timezone.now()
        job.status = IngestionJobStatus.RUNNING
        job.save()

        naive_since = since.replace(tzinfo=None, microsecond=0)
        since_str = naive_since.isoformat(sep=" ")
        response = self.client.get(f"/v1/jobs/?updated_after={since_str}")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data}
        self.assertIn(job.id, ids)

    def test_updated_after_z_suffix(self):
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        since = timezone.now().astimezone(dt_timezone.utc)
        job.status = IngestionJobStatus.RUNNING
        job.save()

        since_str = since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        response = self.client.get(f"/v1/jobs/?updated_after={since_str}")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data}
        self.assertIn(job.id, ids)

    def test_external_uuid_filter(self):
        job_match = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        job_other = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        external_uuid = uuid.uuid4()
        job_match.external_uuid = external_uuid
        job_match.save()

        response = self.client.get(f"/v1/jobs/?external_uuid={external_uuid}")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data}
        self.assertIn(job_match.id, ids)
        self.assertNotIn(job_other.id, ids)

    def test_updated_after_invalid_returns_empty(self):
        response = self.client.get("/v1/jobs/?updated_after=not-a-date")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
