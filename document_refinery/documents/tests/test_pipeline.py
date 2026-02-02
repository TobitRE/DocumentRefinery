import os
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from authn.models import APIKey, Tenant
from documents.models import Artifact, ArtifactKind, Document, IngestionJob, IngestionJobStatus, IngestionStage
from documents.tasks import docling_convert_task, export_artifacts_task, scan_pdf_task


class TestPipelineTasks(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:write", "documents:read"],
            active=True,
        )

    def _make_doc_job(self, data_root: str):
        doc = Document(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="",
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="pending",
            status="UPLOADED",
        )
        relpath = os.path.join("uploads", "quarantine", str(self.tenant.id), f"{doc.uuid}.pdf")
        abs_path = os.path.join(data_root, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as handle:
            handle.write(b"%PDF-1.4 fake\n")
        doc.storage_relpath_quarantine = relpath
        doc.save()

        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
        )
        return doc, job

    def test_scan_marks_clean_and_moves_file(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            abs_path = doc.get_quarantine_path()

            with patch("documents.tasks.clamd.ClamdNetworkSocket.scan") as mock_scan:
                mock_scan.return_value = {abs_path: ("OK", "")}
                scan_pdf_task(job.id)

            doc.refresh_from_db()
            job.refresh_from_db()
            self.assertEqual(doc.status, "CLEAN")
            self.assertTrue(doc.storage_relpath_clean)
            self.assertTrue(os.path.exists(doc.get_clean_path()))
            self.assertIsNotNone(job.scan_ms)

    def test_scan_marks_infected(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            abs_path = doc.get_quarantine_path()

            with patch("documents.tasks.clamd.ClamdNetworkSocket.scan") as mock_scan:
                mock_scan.return_value = {abs_path: ("FOUND", "Eicar-Test-Signature")}
                with self.assertRaises(RuntimeError):
                    scan_pdf_task(job.id)

            doc.refresh_from_db()
            job.refresh_from_db()
            self.assertEqual(doc.status, "INFECTED")
            self.assertEqual(job.status, IngestionJobStatus.QUARANTINED)

    def test_scan_invalid_response_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)

            with patch("documents.tasks.clamd.ClamdNetworkSocket.scan") as mock_scan:
                mock_scan.return_value = None
                with self.assertRaises(RuntimeError):
                    scan_pdf_task(job.id)

            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.FAILED)
            self.assertEqual(job.error_code, "CLAMAV_INVALID_RESPONSE")

    def test_convert_and_export_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling.datamodel.document import DoclingDocument

            with patch("documents.tasks.DocumentConverter.convert") as mock_convert:
                mock_convert.return_value = DummyResult(DoclingDocument(name="test"))
                docling_convert_task(job.id)

            export_artifacts_task(job.id)
            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.RUNNING)
            kinds = set(Artifact.objects.filter(job=job).values_list("kind", flat=True))
            self.assertIn(ArtifactKind.DOCLING_JSON, kinds)


class TestCleanupTasks(TestCase):
    def setUp(self):
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

    def test_cleanup_expired_documents_removes_artifact_files(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = Document.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                original_filename="sample.pdf",
                sha256="c" * 64,
                mime_type="application/pdf",
                size_bytes=10,
                storage_relpath_quarantine="uploads/quarantine/c/c.pdf",
                expires_at=timezone.now(),
            )
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.FAILED,
                stage=IngestionStage.EXPORTING,
            )
            relpath = "artifacts/a/b/docling.json"
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"{}")
            Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=job,
                kind=ArtifactKind.DOCLING_JSON,
                storage_relpath=relpath,
                checksum_sha256="d" * 64,
                size_bytes=2,
            )

            from documents.tasks import cleanup_expired_documents

            cleanup_expired_documents()
            self.assertFalse(os.path.exists(abs_path))
