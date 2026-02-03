import base64
import json
import os
import tempfile
import zipfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from authn.models import APIKey, Tenant
from documents.models import Artifact, ArtifactKind, Document, IngestionJob, IngestionJobStatus, IngestionStage
from documents.tasks import docling_convert_task, export_artifacts_task, scan_pdf_task
from docling.datamodel.base_models import InputFormat


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

    def test_convert_uses_profile_pipeline_options(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()

            job.profile = "fast_text"
            job.save(update_fields=["profile"])

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling.datamodel.document import DoclingDocument

            captured = {}

            class DummyConverter:
                def __init__(self, *args, **kwargs):
                    captured["format_options"] = kwargs.get("format_options")

                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with patch("documents.tasks.DocumentConverter", DummyConverter):
                docling_convert_task(job.id)

            format_options = captured.get("format_options")
            self.assertIsNotNone(format_options)
            pdf_option = format_options.get(InputFormat.PDF)
            self.assertIsNotNone(pdf_option)
            pipeline_options = pdf_option.pipeline_options
            self.assertFalse(pipeline_options.do_ocr)
            self.assertFalse(pipeline_options.do_table_structure)

    def test_export_artifacts_writes_chunks_and_figures(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()

            job.options_json = {
                "exports": ["markdown", "text", "doctags", "chunks_json", "figures_zip"]
            }
            job.save(update_fields=["options_json"])

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling.datamodel.document import DoclingDocument
            from docling_core.types.doc import ImageRef, PictureItem, Size

            image_bytes = b"fakeimage"
            image_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("utf-8")
            docling_doc = DoclingDocument(name="test")
            docling_doc.pictures.append(
                PictureItem(
                    self_ref="#/pictures/0",
                    image=ImageRef(
                        mimetype="image/png",
                        dpi=72,
                        size=Size(width=1, height=1),
                        uri=image_uri,
                    ),
                )
            )

            with patch("documents.tasks.DocumentConverter.convert") as mock_convert:
                mock_convert.return_value = DummyResult(docling_doc)
                docling_convert_task(job.id)

            export_artifacts_task(job.id)

            kinds = set(Artifact.objects.filter(job=job).values_list("kind", flat=True))
            self.assertIn(ArtifactKind.CHUNKS_JSON, kinds)
            self.assertIn(ArtifactKind.FIGURES_ZIP, kinds)

            chunks = Artifact.objects.get(job=job, kind=ArtifactKind.CHUNKS_JSON)
            chunks_path = os.path.join(tmpdir, chunks.storage_relpath)
            with open(chunks_path, "rb") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
            self.assertEqual(payload.get("format"), "doctags")
            self.assertIn("content", payload)

            figures = Artifact.objects.get(job=job, kind=ArtifactKind.FIGURES_ZIP)
            figures_path = os.path.join(tmpdir, figures.storage_relpath)
            with zipfile.ZipFile(figures_path, "r") as archive:
                names = archive.namelist()
            self.assertEqual(len(names), 1)


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
