import base64
import json
import os
import tempfile
import zipfile
from enum import Enum
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from authn.models import APIKey, Tenant
from documents.formats import DOCX
from documents.models import Artifact, ArtifactKind, Document, IngestionJob, IngestionJobStatus, IngestionStage
from documents.docling_options import (
    build_pdf_pipeline_options,
    normalize_docling_options,
    resolve_effective_options,
    validate_docling_options_for_input_format,
)
from documents.profiles import PROFILE_NAMES, build_profile_pipeline_options
from documents.tasks import (
    DOCLING_UNLIMITED,
    docling_convert_task,
    export_artifacts_task,
    scan_pdf_task,
)


class DummyInputFormat(Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"


class DummyPdfFormatOption:
    def __init__(self, pipeline_options=None):
        self.pipeline_options = pipeline_options


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

    def _make_doc_job(
        self,
        data_root: str,
        *,
        original_filename: str = "sample.pdf",
        mime_type: str = "application/pdf",
        extension: str = ".pdf",
        content: bytes = b"%PDF-1.4 fake\n",
    ):
        doc = Document(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename=original_filename,
            sha256="",
            mime_type=mime_type,
            size_bytes=10,
            storage_relpath_quarantine="pending",
            status="UPLOADED",
        )
        relpath = os.path.join(
            "uploads", "quarantine", str(self.tenant.id), f"{doc.uuid}{extension}"
        )
        abs_path = os.path.join(data_root, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as handle:
            handle.write(content)
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
            self.assertTrue(doc.storage_relpath_clean.endswith(".pdf"))
            self.assertTrue(os.path.exists(doc.get_clean_path()))
            self.assertIsNotNone(job.scan_ms)

    def test_scan_preserves_office_extension_when_moving_file(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(
                tmpdir,
                original_filename="sample.docx",
                mime_type=DOCX.primary_mime_type,
                extension=".docx",
                content=b"fake docx",
            )
            abs_path = doc.get_quarantine_path()

            with patch("documents.tasks.clamd.ClamdNetworkSocket.scan") as mock_scan:
                mock_scan.return_value = {abs_path: ("OK", "")}
                scan_pdf_task(job.id)

            doc.refresh_from_db()
            self.assertTrue(doc.storage_relpath_clean.endswith(".docx"))

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

    def test_convert_rejects_disabled_ocr_engine_before_docling(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            _doc, job = self._make_doc_job(tmpdir)
            job.options_json = {"ocr_options": {"kind": "tesseract"}}
            job.save(update_fields=["options_json"])

            with patch("documents.tasks._load_docling_converter") as load_mock:
                docling_convert_task(job.id)

            load_mock.assert_not_called()
            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.FAILED)
            self.assertEqual(job.error_code, "INVALID_OPTIONS")
            self.assertIn("not enabled", job.error_message)

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

            from docling_core.types.doc import DoclingDocument

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
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

            from docling_core.types.doc import DoclingDocument

            captured = {}

            class DummyConverter:
                def __init__(self, *args, **kwargs):
                    captured["format_options"] = kwargs.get("format_options")

                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                docling_convert_task(job.id)

            format_options = captured.get("format_options")
            self.assertIsNotNone(format_options)
            pdf_option = format_options.get(DummyInputFormat.PDF)
            self.assertIsNotNone(pdf_option)
            pipeline_options = pdf_option.pipeline_options
            self.assertFalse(pipeline_options.do_ocr)
            self.assertFalse(pipeline_options.do_table_structure)

    def test_convert_uses_office_input_format(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(
                tmpdir,
                original_filename="sample.docx",
                mime_type=DOCX.primary_mime_type,
                extension=".docx",
                content=b"fake docx",
            )
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.docx")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"fake docx")
            doc.storage_relpath_clean = clean_relpath
            doc.save()

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling_core.types.doc import DoclingDocument

            captured = {}

            class DummyConverter:
                def __init__(self, *args, **kwargs):
                    captured["allowed_formats"] = kwargs.get("allowed_formats")
                    captured["format_options"] = kwargs.get("format_options")

                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                docling_convert_task(job.id)

            self.assertEqual(captured["allowed_formats"], [DummyInputFormat.DOCX])
            self.assertIn(DummyInputFormat.DOCX, captured["format_options"])

    def test_convert_handles_partial_success_as_failure(self):
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
                    self.status = "partial_success"
                    self.errors = [{"message": "page failed"}]

            from docling_core.types.doc import DoclingDocument

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                with self.assertRaises(RuntimeError):
                    docling_convert_task(job.id)

            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.FAILED)
            self.assertEqual(job.error_code, "DOCLING_PARTIAL_SUCCESS")
            self.assertEqual(job.error_details_json["docling_status"], "partial_success")

    def test_convert_passes_zero_limits_as_docling_unlimited(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()
            job.options_json = {"max_num_pages": 0, "max_file_size": 0}
            job.save(update_fields=["options_json"])

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling_core.types.doc import DoclingDocument

            captured = {}

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    captured["kwargs"] = kwargs
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                docling_convert_task(job.id)

            self.assertEqual(captured["kwargs"]["max_num_pages"], DOCLING_UNLIMITED)
            self.assertEqual(captured["kwargs"]["max_file_size"], DOCLING_UNLIMITED)

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

            from docling_core.types.doc import DoclingDocument
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

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    return DummyResult(docling_doc)

            with self._patch_docling_converter(DummyConverter):
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

    def test_export_artifacts_respects_empty_exports(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()
            job.options_json = {"exports": []}
            job.save(update_fields=["options_json"])

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling_core.types.doc import DoclingDocument

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                docling_convert_task(job.id)

            export_artifacts_task(job.id)

            kinds = set(Artifact.objects.filter(job=job).values_list("kind", flat=True))
            self.assertEqual(kinds, {ArtifactKind.DOCLING_JSON})

    def test_export_artifacts_failure_marks_job_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc, job = self._make_doc_job(tmpdir)
            clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
            clean_abs = os.path.join(tmpdir, clean_relpath)
            os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
            with open(clean_abs, "wb") as handle:
                handle.write(b"%PDF-1.4 fake\n")
            doc.storage_relpath_clean = clean_relpath
            doc.save()
            job.options_json = {"exports": ["markdown"]}
            job.save(update_fields=["options_json"])

            class DummyResult:
                def __init__(self, document):
                    self.document = document

            from docling_core.types.doc import DoclingDocument

            class DummyConverter:
                def convert(self, *args, **kwargs):
                    return DummyResult(DoclingDocument(name="test"))

            with self._patch_docling_converter(DummyConverter):
                docling_convert_task(job.id)

            with patch(
                "documents.tasks.DoclingDocument.export_to_markdown",
                side_effect=RuntimeError("markdown failed"),
            ):
                with self.assertRaises(RuntimeError):
                    export_artifacts_task(job.id)

            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.FAILED)
            self.assertEqual(job.error_code, "DOCLING_EXPORT_FAILED")

    def _patch_docling_converter(self, converter):
        class ConverterAdapter(converter):
            def __init__(self, *args, **kwargs):
                try:
                    super().__init__(*args, **kwargs)
                except TypeError:
                    super().__init__()

        return patch(
            "documents.tasks._load_docling_converter",
            return_value=(
                ConverterAdapter,
                {
                    "pdf": DummyPdfFormatOption,
                    "docx": DummyPdfFormatOption,
                    "pptx": DummyPdfFormatOption,
                    "xlsx": DummyPdfFormatOption,
                },
                DummyInputFormat,
            ),
        )

    def test_all_profiles_build_docling_pipeline_options(self):
        for profile in PROFILE_NAMES:
            with self.subTest(profile=profile):
                self.assertIsNotNone(build_profile_pipeline_options(profile))

    def test_effective_options_merge_layers_and_profile_overrides(self):
        self.tenant.docling_options_json = {
            "max_num_pages": 8,
            "unknown_future_option": "kept",
        }
        self.tenant.save(update_fields=["docling_options_json"])
        self.api_key.docling_options_json = {"ocr": True, "exports": ["text"]}
        self.api_key.save(update_fields=["docling_options_json"])

        resolved = resolve_effective_options(
            self.api_key,
            {"ocr_languages": ["de"], "max_file_size": 1024},
            "fast_text",
        )

        effective = resolved["effective_options"]
        self.assertEqual(effective["max_num_pages"], 8)
        self.assertEqual(effective["max_file_size"], 1024)
        self.assertEqual(effective["exports"], ["text", "markdown", "doctags"])
        self.assertFalse(effective["do_ocr"])
        self.assertEqual(effective["ocr_options"]["lang"], ["de"])
        self.assertEqual(effective["unknown_future_option"], "kept")
        self.assertTrue(
            any("unknown_future_option" in warning for warning in resolved["warnings"])
        )

    def test_request_ocr_options_override_profile_defaults(self):
        resolved = resolve_effective_options(
            self.api_key,
            {"ocr_languages": ["de"], "ocr_engine": "rapidocr"},
            "ocr_only",
        )

        ocr_options = resolved["effective_options"]["ocr_options"]
        self.assertEqual(ocr_options["kind"], "rapidocr")
        self.assertEqual(ocr_options["lang"], ["de"])
        self.assertTrue(ocr_options["force_full_page_ocr"])

    def test_profiles_pin_ocr_to_rapidocr(self):
        for profile in ("ocr_only", "structured", "full_vlm"):
            with self.subTest(profile=profile):
                resolved = resolve_effective_options(self.api_key, {}, profile)
                self.assertEqual(resolved["effective_options"]["ocr_options"]["kind"], "rapidocr")

    def test_rejects_disabled_easyocr_effective_options(self):
        with self.assertRaises(ValidationError):
            resolve_effective_options(
                self.api_key,
                {"ocr_options": {"kind": "easyocr"}},
                "ocr_only",
            )

    def test_normalize_legacy_ocr_keys(self):
        normalized, warnings = normalize_docling_options(
            {"ocr": True, "ocr_languages": ["en"], "ocr_engine": "rapidocr"}
        )
        self.assertTrue(normalized["do_ocr"])
        self.assertEqual(normalized["ocr_options"]["lang"], ["en"])
        self.assertEqual(normalized["ocr_options"]["kind"], "rapidocr")
        self.assertTrue(any("ocr" in warning for warning in warnings))

    def test_office_input_allows_disabled_pdf_only_defaults(self):
        validate_docling_options_for_input_format(
            {
                "do_ocr": False,
                "do_table_structure": False,
                "generate_parsed_pages": False,
                "generate_picture_images": False,
                "force_full_page_ocr": False,
                "ocr_engine": "rapidocr",
                "ocr_languages": ["de"],
                "ocr_options": {
                    "kind": "rapidocr",
                    "lang": ["de"],
                    "force_full_page_ocr": False,
                },
                "images_scale": 2.0,
            },
            "docx",
        )

    def test_office_input_rejects_active_pdf_only_options(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_docling_options_for_input_format(
                {
                    "do_ocr": True,
                    "do_table_structure": True,
                    "ocr_engine": "rapidocr",
                },
                "docx",
            )
        message = str(ctx.exception)
        self.assertIn("do_ocr", message)
        self.assertIn("do_table_structure", message)
        self.assertIn("ocr_engine", message)

    def test_build_pdf_pipeline_options_from_effective_options(self):
        pipeline_options = build_pdf_pipeline_options(
            {
                "do_ocr": True,
                "ocr_options": {"kind": "auto", "lang": ["en"]},
                "do_table_structure": True,
                "generate_parsed_pages": True,
            }
        )
        self.assertTrue(pipeline_options.do_ocr)
        self.assertTrue(pipeline_options.do_table_structure)


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
