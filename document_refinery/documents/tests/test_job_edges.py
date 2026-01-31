from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import Artifact, ArtifactKind, Document, IngestionJob, IngestionJobStatus, IngestionStage
from documents.tasks import finalize_job_task


class TestJobCancelSemantics(TestCase):
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

    def test_finalize_does_not_override_canceled(self):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=doc,
            status=IngestionJobStatus.CANCELED,
            stage=IngestionStage.FINALIZING,
        )

        finalize_job_task(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestionJobStatus.CANCELED)


class TestJobRetryArtifacts(TestCase):
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

    def test_retry_clears_existing_artifacts(self):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="b" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/b/b.pdf",
        )
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=doc,
            status=IngestionJobStatus.FAILED,
            stage=IngestionStage.EXPORTING,
        )
        Artifact.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            job=job,
            kind=ArtifactKind.DOCLING_JSON,
            storage_relpath="artifacts/a/b/docling.json",
            checksum_sha256="c" * 64,
            size_bytes=10,
        )

        with patch("documents.views.start_ingestion_pipeline"):
            response = self.client.post(f"/v1/jobs/{job.id}/retry/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Artifact.objects.filter(job=job).count(), 0)
