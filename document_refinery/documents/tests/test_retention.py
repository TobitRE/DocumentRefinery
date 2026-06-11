import hashlib
import os
import tempfile
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import (
    Artifact,
    ArtifactKind,
    Document,
    DocumentStatus,
    IngestionJob,
)
from documents.tasks import (
    _write_artifact,
    cleanup_expired_artifacts,
    cleanup_expired_documents,
)


class TestRetentionSchedule(TestCase):
    def test_celery_beat_schedules_retention_tasks_hourly(self):
        schedule = settings.CELERY_BEAT_SCHEDULE

        self.assertEqual(
            schedule["cleanup-expired-artifacts-hourly"]["task"],
            "documents.tasks.cleanup_expired_artifacts",
        )
        self.assertEqual(
            schedule["cleanup-expired-artifacts-hourly"]["schedule"],
            3600.0,
        )
        self.assertEqual(
            schedule["cleanup-expired-documents-hourly"]["task"],
            "documents.tasks.cleanup_expired_documents",
        )
        self.assertEqual(
            schedule["cleanup-expired-documents-hourly"]["schedule"],
            3600.0,
        )


class RetentionTestCase(TestCase):
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
            scopes=["documents:write", "documents:read", "artifacts:read"],
            active=True,
        )

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def _upload_pdf(self, content: bytes = b"%PDF-1.4\n%fake\n"):
        self._auth()
        upload = SimpleUploadedFile(
            "sample.pdf",
            content,
            content_type="application/pdf",
        )
        return self.client.post("/v1/documents/", {"file": upload}, format="multipart")

    def _make_doc(self, **kwargs):
        defaults = {
            "tenant": self.tenant,
            "created_by_key": self.api_key,
            "original_filename": "sample.pdf",
            "sha256": hashlib.sha256(os.urandom(8)).hexdigest(),
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "storage_relpath_quarantine": "uploads/quarantine/missing.pdf",
        }
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def _make_job(self, doc: Document, **kwargs):
        defaults = {
            "tenant": self.tenant,
            "created_by_key": self.api_key,
            "document": doc,
        }
        defaults.update(kwargs)
        return IngestionJob.objects.create(**defaults)


class TestRetentionCreation(RetentionTestCase):
    def assertExpiresAtDaysFromNow(self, expires_at, days: int):
        self.assertIsNotNone(expires_at)
        expected = timezone.now() + timedelta(days=days)
        self.assertLess(abs((expires_at - expected).total_seconds()), 5)

    def test_document_upload_uses_default_retention(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            DOCUMENT_RETENTION_DAYS=7,
        ):
            response = self._upload_pdf()

        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.data["id"])
        self.assertExpiresAtDaysFromNow(doc.expires_at, 7)

    def test_document_upload_tenant_zero_override_is_unlimited(self):
        self.tenant.document_retention_days = 0
        self.tenant.save(update_fields=["document_retention_days"])

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            DOCUMENT_RETENTION_DAYS=7,
        ):
            response = self._upload_pdf()

        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.data["id"])
        self.assertIsNone(doc.expires_at)

    def test_document_upload_tenant_override_wins(self):
        self.tenant.document_retention_days = 2
        self.tenant.save(update_fields=["document_retention_days"])

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            DOCUMENT_RETENTION_DAYS=30,
        ):
            response = self._upload_pdf()

        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.data["id"])
        self.assertExpiresAtDaysFromNow(doc.expires_at, 2)

    def test_artifact_create_uses_default_retention(self):
        doc = self._make_doc()
        job = self._make_job(doc)
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            ARTIFACT_RETENTION_DAYS=5,
        ):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(job.id), "doc.txt")
            artifact = _write_artifact(job, ArtifactKind.TEXT, relpath, b"data", "text/plain")

        self.assertExpiresAtDaysFromNow(artifact.expires_at, 5)

    def test_artifact_create_tenant_override_wins(self):
        self.tenant.artifact_retention_days = 3
        self.tenant.save(update_fields=["artifact_retention_days"])
        doc = self._make_doc()
        job = self._make_job(doc)
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            ARTIFACT_RETENTION_DAYS=30,
        ):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(job.id), "doc.txt")
            artifact = _write_artifact(job, ArtifactKind.TEXT, relpath, b"data", "text/plain")

        self.assertExpiresAtDaysFromNow(artifact.expires_at, 3)

    def test_artifact_create_tenant_zero_override_is_unlimited(self):
        self.tenant.artifact_retention_days = 0
        self.tenant.save(update_fields=["artifact_retention_days"])
        doc = self._make_doc()
        job = self._make_job(doc)
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            ARTIFACT_RETENTION_DAYS=30,
        ):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(job.id), "doc.txt")
            artifact = _write_artifact(job, ArtifactKind.TEXT, relpath, b"data", "text/plain")

        self.assertIsNone(artifact.expires_at)


class TestRetentionCleanup(RetentionTestCase):
    def _write_file(self, data_root: str, relpath: str, content: bytes = b"data") -> str:
        abs_path = os.path.join(data_root, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as handle:
            handle.write(content)
        return abs_path

    def test_cleanup_expired_artifacts_removes_empty_job_and_tenant_dirs(self):
        doc = self._make_doc()
        job = self._make_job(doc)

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(job.id), "doc.txt")
            abs_path = self._write_file(tmpdir, relpath)
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=4,
                expires_at=timezone.now() - timedelta(days=1),
            )

            result = cleanup_expired_artifacts.apply()

            self.assertEqual(result.result, 1)
            self.assertFalse(os.path.exists(abs_path))
            self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())
            self.assertFalse(
                os.path.exists(os.path.join(tmpdir, "artifacts", str(self.tenant.id), str(job.id)))
            )
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "artifacts", str(self.tenant.id))))

    def test_cleanup_expired_documents_removes_files_and_empty_storage_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            quarantine_relpath = os.path.join(
                "uploads", "quarantine", str(self.tenant.id), "doc.pdf"
            )
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), "doc.pdf")
            source_relpath = os.path.join(
                "uploads", "quarantine", str(self.tenant.id), "jobs", "source.pdf"
            )
            quarantine_path = self._write_file(tmpdir, quarantine_relpath)
            clean_path = self._write_file(tmpdir, clean_relpath)
            source_path = self._write_file(tmpdir, source_relpath)

            doc = self._make_doc(
                storage_relpath_quarantine=quarantine_relpath,
                storage_relpath_clean=clean_relpath,
                expires_at=timezone.now() - timedelta(days=1),
            )
            job = self._make_job(doc, source_relpath=source_relpath)
            artifact_relpath = os.path.join(
                "artifacts", str(self.tenant.id), str(job.id), "docling.json"
            )
            artifact_path = self._write_file(tmpdir, artifact_relpath, b"{}")
            Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=job,
                kind=ArtifactKind.DOCLING_JSON,
                storage_relpath=artifact_relpath,
                checksum_sha256="d" * 64,
                size_bytes=2,
            )

            result = cleanup_expired_documents.apply()

            self.assertEqual(result.result, 1)
            self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
            self.assertFalse(os.path.exists(quarantine_path))
            self.assertFalse(os.path.exists(clean_path))
            self.assertFalse(os.path.exists(source_path))
            self.assertFalse(os.path.exists(artifact_path))
            self.assertFalse(
                os.path.exists(os.path.join(tmpdir, "uploads", "quarantine", str(self.tenant.id)))
            )
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "uploads", "clean", str(self.tenant.id))))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "artifacts", str(self.tenant.id))))

    def test_cleanup_infected_quarantine_respects_tenant_retention(self):
        self.tenant.infected_quarantine_retention_days = 1
        self.tenant.save(update_fields=["infected_quarantine_retention_days"])

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            INFECTED_QUARANTINE_RETENTION_DAYS=0,
        ):
            relpath = os.path.join("uploads", "quarantine", str(self.tenant.id), "bad.pdf")
            abs_path = self._write_file(tmpdir, relpath)
            doc = self._make_doc(
                storage_relpath_quarantine=relpath,
                status=DocumentStatus.INFECTED,
                infected_at=timezone.now() - timedelta(days=2),
            )

            result = cleanup_expired_documents.apply()

            self.assertEqual(result.result, 1)
            self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
            self.assertFalse(os.path.exists(abs_path))
            self.assertFalse(
                os.path.exists(os.path.join(tmpdir, "uploads", "quarantine", str(self.tenant.id)))
            )

    def test_cleanup_infected_quarantine_keeps_files_when_unlimited(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            INFECTED_QUARANTINE_RETENTION_DAYS=0,
        ):
            relpath = os.path.join("uploads", "quarantine", str(self.tenant.id), "bad.pdf")
            abs_path = self._write_file(tmpdir, relpath)
            doc = self._make_doc(
                storage_relpath_quarantine=relpath,
                status=DocumentStatus.INFECTED,
                infected_at=timezone.now() - timedelta(days=30),
            )

            result = cleanup_expired_documents.apply()

            self.assertEqual(result.result, 0)
            self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
            self.assertTrue(os.path.exists(abs_path))
