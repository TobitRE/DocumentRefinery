import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from authn.models import APIKey, Tenant
from documents.models import Artifact, ArtifactKind, Document, IngestionJob
from documents.tasks import cleanup_expired_artifacts, start_ingestion_pipeline


class TestTaskHelpers(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:write"],
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

    def test_start_ingestion_pipeline_sets_root_task_id(self):
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
        )

        parent = SimpleNamespace(id="root-id", parent=None)
        result = SimpleNamespace(id="child-id", parent=parent)

        class FakeChain:
            def apply_async(self, queue=None):
                return result

        with patch("documents.tasks.chain", return_value=FakeChain()):
            start_ingestion_pipeline(job.id)

        job.refresh_from_db()
        self.assertEqual(job.celery_task_id, "root-id")

    def test_cleanup_expired_artifacts_removes_files(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), "1", "doc.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"data")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=IngestionJob.objects.create(
                    tenant=self.tenant,
                    created_by_key=self.api_key,
                    document=self.doc,
                ),
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=4,
                expires_at=timezone.now() - timezone.timedelta(days=1),
            )

            result = cleanup_expired_artifacts.apply()
            self.assertEqual(result.result, 1)
            self.assertFalse(os.path.exists(abs_path))
            self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())
