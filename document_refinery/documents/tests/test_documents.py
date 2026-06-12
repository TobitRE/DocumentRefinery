import hashlib
import io
import os
import tempfile
import uuid
import zipfile
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from authn.models import APIKey, Tenant
from documents.formats import (
    DOCX,
    OOXML_MAX_COMPRESSION_RATIO,
    OOXML_MAX_ZIP_ENTRIES,
    OOXML_ZIP_SAFETY_MESSAGE,
    PPTX,
    XLSX,
)
from documents.models import (
    Artifact,
    ArtifactKind,
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)


class TestDocumentUpload(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:write", "documents:read"],
            active=True,
        )

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def _ooxml_bytes(self, required_member: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types></Types>")
            archive.writestr("_rels/.rels", "<Relationships></Relationships>")
            archive.writestr(required_member, "<root>Hello</root>")
        return buffer.getvalue()

    def _compressed_ooxml_bomb_bytes(self, required_member: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types></Types>")
            archive.writestr("_rels/.rels", "<Relationships></Relationships>")
            archive.writestr(required_member, "<root>Hello</root>")
            archive.writestr(
                "word/repeated.xml",
                b"a" * (OOXML_MAX_COMPRESSION_RATIO * 4096),
            )
        return buffer.getvalue()

    def _many_entry_ooxml_bytes(self, required_member: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("[Content_Types].xml", "<Types></Types>")
            archive.writestr("_rels/.rels", "<Relationships></Relationships>")
            archive.writestr(required_member, "<root>Hello</root>")
            for index in range(OOXML_MAX_ZIP_ENTRIES + 1):
                archive.writestr(f"word/empty-{index}.xml", b"")
        return buffer.getvalue()

    def test_upload_writes_file_and_hash(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        expected_hash = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 201)

            doc = Document.objects.get(pk=response.data["id"])
            self.assertEqual(doc.sha256, expected_hash)
            self.assertEqual(doc.size_bytes, len(content))
            self.assertTrue(doc.storage_relpath_quarantine)
            self.assertTrue(doc.storage_relpath_quarantine.endswith(".pdf"))
            self.assertTrue(os.path.exists(doc.get_quarantine_path()))

    def test_upload_accepts_office_open_xml_formats(self):
        cases = [
            ("sample.docx", DOCX, "word/document.xml"),
            ("sample.pptx", PPTX, "ppt/presentation.xml"),
            ("sample.xlsx", XLSX, "xl/workbook.xml"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            for filename, document_format, required_member in cases:
                with self.subTest(filename=filename):
                    content = self._ooxml_bytes(required_member)
                    upload = SimpleUploadedFile(
                        filename,
                        content,
                        content_type=document_format.primary_mime_type,
                    )
                    response = self.client.post(
                        "/v1/documents/", {"file": upload}, format="multipart"
                    )
                    self.assertEqual(response.status_code, 201)
                    doc = Document.objects.get(pk=response.data["id"])
                    self.assertEqual(doc.mime_type, document_format.primary_mime_type)
                    self.assertTrue(
                        doc.storage_relpath_quarantine.endswith(
                            document_format.primary_extension
                        )
                    )

    def test_upload_rejects_spoofed_office_open_xml(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile(
                "sample.docx",
                b"not a zip",
                content_type=DOCX.primary_mime_type,
            )
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.data["error_code"], "INVALID_DOCUMENT")

    def test_upload_rejects_compressed_office_open_xml_zip_bomb(self):
        content = self._compressed_ooxml_bomb_bytes("word/document.xml")
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile(
                "sample.docx",
                content,
                content_type=DOCX.primary_mime_type,
            )
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.data["error_code"], "INVALID_DOCUMENT")
            self.assertEqual(response.data["message"], OOXML_ZIP_SAFETY_MESSAGE)
            self.assertFalse(Document.objects.exists())

    def test_upload_rejects_too_many_ooxml_entries_before_zipfile_parse(self):
        content = self._many_entry_ooxml_bytes("word/document.xml")
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile(
                "sample.docx",
                content,
                content_type=DOCX.primary_mime_type,
            )
            with patch(
                "documents.formats.zipfile.ZipFile",
                side_effect=AssertionError("ZipFile should not parse this archive"),
            ):
                response = self.client.post(
                    "/v1/documents/", {"file": upload}, format="multipart"
                )
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.data["error_code"], "INVALID_DOCUMENT")
            self.assertEqual(response.data["message"], OOXML_ZIP_SAFETY_MESSAGE)
            self.assertFalse(Document.objects.exists())

    def test_upload_rejects_large_file(self):
        content = b"%PDF-1.4\n" + (b"x" * (1024 * 1024))
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir, UPLOAD_MAX_SIZE_MB=1
        ):
            self._auth()
            upload = SimpleUploadedFile("big.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 413)

    def test_upload_rejects_non_pdf(self):
        content = b"not a pdf"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.txt", content, content_type="text/plain")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 415)

    def test_upload_respects_api_key_allowed_file_types(self):
        api_key = APIKey.objects.get(tenant=self.tenant)
        api_key.allowed_upload_mime_types = ["application/x-pdf"]
        api_key.save()
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.data["error_code"], "UNSUPPORTED_MEDIA_TYPE")
            self.assertIn("application/x-pdf", response.data["message"])

    def test_upload_accepts_allowed_pdf_alias(self):
        api_key = APIKey.objects.get(tenant=self.tenant)
        api_key.allowed_upload_mime_types = ["application/x-pdf"]
        api_key.save()
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        expected_hash = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/x-pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 201)
            doc = Document.objects.get(pk=response.data["id"])
            self.assertEqual(doc.sha256, expected_hash)

    def test_upload_rejects_spoofed_pdf(self):
        content = b"not really a pdf"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.data["error_code"], "INVALID_PDF")

    def test_upload_streamed_file_too_large(self):
        content = b"%PDF-1.4\n" + (b"x" * (1024 * 1024 + 4))
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir, UPLOAD_MAX_SIZE_MB=1
        ):
            self._auth()
            upload = SimpleUploadedFile("big.pdf", content, content_type="application/pdf")
            upload.size = 0
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 413)
            upload_dir = os.path.join(tmpdir, "uploads", "quarantine", str(self.tenant.id))
            if os.path.exists(upload_dir):
                self.assertEqual(os.listdir(upload_dir), [])

    def test_upload_rejects_duplicate(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        existing_hash = hashlib.sha256(content).hexdigest()
        api_key = APIKey.objects.get(tenant=self.tenant)
        existing_doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=api_key,
            original_filename="existing.pdf",
            sha256=existing_hash,
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_relpath_quarantine="uploads/quarantine/existing.pdf",
        )
        latest_job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=api_key,
            document=existing_doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
        )
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["error_code"], "DUPLICATE_DOCUMENT")
            self.assertTrue(response.data["duplicate"])
            self.assertEqual(response.data["document_id"], existing_doc.id)
            self.assertEqual(response.data["document_uuid"], str(existing_doc.uuid))
            self.assertEqual(response.data["sha256"], existing_hash)
            self.assertEqual(response.data["latest_job_id"], latest_job.id)
            self.assertEqual(response.data["latest_job_uuid"], str(latest_job.uuid))
            self.assertEqual(response.data["latest_job_status"], IngestionJobStatus.SUCCEEDED)
            self.assertIn(f"/v1/documents/{existing_doc.id}/", response["Location"])

    def test_upload_duplicate_policy_return_existing_returns_200(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        existing_hash = hashlib.sha256(content).hexdigest()
        api_key = APIKey.objects.get(tenant=self.tenant)
        existing_doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=api_key,
            original_filename="existing.pdf",
            sha256=existing_hash,
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_relpath_quarantine="uploads/quarantine/existing.pdf",
        )
        latest_job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=api_key,
            document=existing_doc,
            status=IngestionJobStatus.RUNNING,
            stage=IngestionStage.CONVERTING,
        )
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post(
                "/v1/documents/",
                {"file": upload, "duplicate_policy": "return_existing"},
                format="multipart",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["duplicate"])
        self.assertEqual(response.data["document"]["id"], existing_doc.id)
        self.assertEqual(response.data["document"]["uuid"], str(existing_doc.uuid))
        self.assertEqual(response.data["latest_job"]["id"], latest_job.id)
        self.assertEqual(response.data["latest_job"]["uuid"], str(latest_job.uuid))
        self.assertEqual(response.data["latest_job"]["status"], IngestionJobStatus.RUNNING)
        self.assertEqual(response.data["latest_job"]["stage"], IngestionStage.CONVERTING)
        self.assertNotIn("options_json", response.data["latest_job"])
        self.assertNotIn("runtime_json", response.data["latest_job"])
        self.assertNotIn("worker_hostname", response.data["latest_job"])
        self.assertNotIn("celery_task_id", response.data["latest_job"])

    def test_upload_duplicate_policy_rejects_unknown_value(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post(
                "/v1/documents/",
                {"file": upload, "duplicate_policy": "overwrite"},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)

    def test_upload_duplicate_integrity_error_returns_existing_document_payload(self):
        content = b"%PDF-1.4\n%race\n1 0 obj\n<<>>\nendobj\n"
        existing_hash = hashlib.sha256(content).hexdigest()
        api_key = APIKey.objects.get(tenant=self.tenant)
        original_save = Document.save
        created = {}

        def save_with_concurrent_duplicate(instance, *args, **kwargs):
            if instance.sha256 == existing_hash and "doc" not in created:
                existing_doc = Document(
                    tenant=self.tenant,
                    created_by_key=api_key,
                    original_filename="existing.pdf",
                    sha256=existing_hash,
                    mime_type="application/pdf",
                    size_bytes=len(content),
                    storage_relpath_quarantine="uploads/quarantine/existing.pdf",
                )
                original_save(existing_doc)
                created["doc"] = existing_doc
                raise IntegrityError("duplicate")
            return original_save(instance, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            with patch("documents.views.Document.save", save_with_concurrent_duplicate):
                response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data["duplicate"])
        self.assertEqual(response.data["document_id"], created["doc"].id)
        self.assertEqual(response.data["document_uuid"], str(created["doc"].uuid))
        self.assertEqual(Document.objects.count(), 1)

    def test_upload_rejects_invalid_options(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    "/v1/documents/",
                    {
                        "file": upload,
                        "ingest": "true",
                        "options_json": '{"max_num_pages": "ten"}',
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error_code"], "INVALID_OPTIONS")
            self.assertEqual(Document.objects.count(), 0)

    def test_upload_profile_sets_job_profile_and_exports(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    "/v1/documents/",
                    {"file": upload, "ingest": "true", "profile": "fast_text"},
                    format="multipart",
                )
            self.assertEqual(response.status_code, 201)
            job = Document.objects.get(pk=response.data["id"]).jobs.first()
            self.assertIsNotNone(job)
            self.assertEqual(job.profile, "fast_text")
            self.assertEqual(job.options_json.get("exports"), ["text", "markdown", "doctags"])

    def test_upload_rejects_profile_for_office_document(self):
        content = self._ooxml_bytes("word/document.xml")
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile(
                "sample.docx",
                content,
                content_type=DOCX.primary_mime_type,
            )
            response = self.client.post(
                "/v1/documents/",
                {"file": upload, "ingest": "true", "profile": "fast_text"},
                format="multipart",
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error_code"], "INVALID_OPTIONS")

    def test_office_ingest_accepts_disabled_pdf_only_api_key_defaults(self):
        api_key = APIKey.objects.get(tenant=self.tenant)
        api_key.docling_options_json = {
            "do_ocr": False,
            "do_table_structure": False,
            "ocr_engine": "rapidocr",
        }
        api_key.save(update_fields=["docling_options_json"])
        content = self._ooxml_bytes("word/document.xml")
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile(
                "sample.docx",
                content,
                content_type=DOCX.primary_mime_type,
            )
            with patch("documents.views.start_ingestion_pipeline") as start:
                response = self.client.post(
                    "/v1/documents/",
                    {"file": upload, "ingest": "true"},
                    format="multipart",
                )
            self.assertEqual(response.status_code, 201)
            doc = Document.objects.get(pk=response.data["id"])
            job = doc.jobs.first()
            self.assertIsNotNone(job)
            self.assertFalse(job.options_json["do_ocr"])
            self.assertFalse(job.options_json["do_table_structure"])
            self.assertEqual(job.options_json["ocr_options"]["kind"], "rapidocr")
            start.assert_called_once()

    def test_upload_profile_overrides_exports(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    "/v1/documents/",
                    {
                        "file": upload,
                        "ingest": "true",
                        "profile": "fast_text",
                        "options_json": '{"max_num_pages": 12, "exports": ["text"]}',
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 201)
            job = Document.objects.get(pk=response.data["id"]).jobs.first()
            self.assertIsNotNone(job)
            self.assertEqual(job.options_json.get("max_num_pages"), 12)
            self.assertEqual(job.options_json.get("exports"), ["text", "markdown", "doctags"])

    def test_upload_ingest_queue_failure_rolls_back_state(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            with patch(
                "documents.views.start_ingestion_pipeline",
                side_effect=RuntimeError("broker down"),
            ):
                response = self.client.post(
                    "/v1/documents/",
                    {"file": upload, "ingest": "true"},
                    format="multipart",
                )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data["error_code"], "QUEUE_UNAVAILABLE")
            self.assertEqual(Document.objects.count(), 0)
            self.assertEqual(IngestionJob.objects.count(), 0)
            upload_dir = os.path.join(tmpdir, "uploads", "quarantine", str(self.tenant.id))
            if os.path.exists(upload_dir):
                self.assertEqual(os.listdir(upload_dir), [])

    def test_upload_rejects_invalid_profile(self):
        content = b"%PDF-1.4\n%fake\n1 0 obj\n<<>>\nendobj\n"
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post(
                "/v1/documents/",
                {"file": upload, "ingest": "true", "profile": "unknown"},
                format="multipart",
            )
            self.assertEqual(response.status_code, 400)


class TestDoclingMetadataAPI(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            name="Acme",
            slug="acme",
            docling_options_json={"max_num_pages": 9, "future_key": True},
        )
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:write"],
            active=True,
            docling_options_json={"ocr": True},
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def test_profiles_endpoint_returns_backend_catalog(self):
        response = self.client.get("/v1/docling/profiles/")
        self.assertEqual(response.status_code, 200)
        profiles = {item["name"]: item for item in response.data["profiles"]}
        self.assertIn("fast_text", profiles)
        self.assertIn("full_vlm", profiles)
        self.assertFalse(profiles["full_vlm"]["capabilities"]["vlm_pipeline"])
        self.assertTrue(profiles["full_vlm"]["warnings"])

    def test_capabilities_endpoint_marks_planned_features(self):
        response = self.client.get("/v1/docling/capabilities/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["input_formats"]["implemented"],
            ["pdf", "docx", "pptx", "xlsx"],
        )
        self.assertIn("multi_format_upload", response.data["features"]["implemented"])
        self.assertIn("real_chunking", response.data["features"]["planned"])
        self.assertIn("vlm_pipeline", response.data["features"]["planned"])
        schema = {item["key"]: item for item in response.data["options_schema"]}
        self.assertEqual(schema["ocr_engine"]["choices"], ["auto", "rapidocr"])

    def test_options_resolve_merges_layers_and_warns_unknown_keys(self):
        response = self.client.post(
            "/v1/docling/options/resolve/",
            {
                "profile": "fast_text",
                "options_json": {"ocr_languages": ["de"], "max_file_size": 2048},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        effective = response.data["effective_options"]
        self.assertEqual(effective["max_num_pages"], 9)
        self.assertEqual(effective["max_file_size"], 2048)
        self.assertFalse(effective["do_ocr"])
        self.assertEqual(effective["exports"], ["text", "markdown", "doctags"])
        self.assertEqual(effective["ocr_options"]["lang"], ["de"])
        self.assertTrue(any("future_key" in warning for warning in response.data["warnings"]))

    def test_docling_metadata_endpoints_require_allowed_scope(self):
        raw_key, prefix, key_hash = APIKey.generate_key()
        APIKey.objects.create(
            tenant=self.tenant,
            name="ReadOnly",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")
        response = self.client.get("/v1/docling/profiles/")
        self.assertEqual(response.status_code, 403)


class TestDocumentScope(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        raw_key_a, prefix_a, key_hash_a = APIKey.generate_key()
        self.raw_key_a = raw_key_a
        APIKey.objects.create(
            tenant=self.tenant_a,
            name="Key A",
            prefix=prefix_a,
            key_hash=key_hash_a,
            scopes=["documents:read"],
            active=True,
        )

        raw_key_b, prefix_b, key_hash_b = APIKey.generate_key()
        self.raw_key_b = raw_key_b
        APIKey.objects.create(
            tenant=self.tenant_b,
            name="Key B",
            prefix=prefix_b,
            key_hash=key_hash_b,
            scopes=["documents:read"],
            active=True,
        )

        Document.objects.create(
            tenant=self.tenant_a,
            created_by_key=APIKey.objects.get(tenant=self.tenant_a),
            original_filename="a.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )

    def test_key_cannot_access_other_tenant_docs(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key_b}")
        response = self.client.get("/v1/documents/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class TestDocumentCompare(TestCase):
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
            scopes=["documents:write", "jobs:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def _make_document(self, data_root: str):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="c" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )
        clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
        clean_abs = os.path.join(data_root, clean_relpath)
        os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
        with open(clean_abs, "wb") as handle:
            handle.write(b"%PDF-1.4 fake\n")
        doc.storage_relpath_clean = clean_relpath
        doc.save(update_fields=["storage_relpath_clean"])
        return doc

    def test_compare_creates_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document(tmpdir)
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    f"/v1/documents/{doc.id}/compare/",
                    {"profiles": ["fast_text", "structured"]},
                    format="json",
                )

            self.assertEqual(response.status_code, 201)
            comparison_id = response.data.get("comparison_id")
            self.assertTrue(comparison_id)
            jobs = response.data.get("jobs")
            self.assertEqual(len(jobs), 2)

            job_profiles = {item["profile"] for item in jobs}
            self.assertEqual(job_profiles, {"fast_text", "structured"})

            stored = IngestionJob.objects.filter(comparison_id=comparison_id)
            self.assertEqual(stored.count(), 2)
            for job in stored:
                self.assertTrue(job.source_relpath)
                self.assertTrue(os.path.exists(os.path.join(tmpdir, job.source_relpath)))
                self.assertEqual(job.status, IngestionJobStatus.QUEUED)
                self.assertEqual(job.stage, IngestionStage.SCANNING)

    def test_compare_missing_source_file(self):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="d" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )
        response = self.client.post(
            f"/v1/documents/{doc.id}/compare/",
            {"profiles": ["fast_text"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "MISSING_SOURCE_FILE")

    def test_compare_queue_failure_rolls_back_created_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document(tmpdir)
            with patch(
                "documents.views.start_ingestion_pipeline",
                side_effect=RuntimeError("broker down"),
            ):
                response = self.client.post(
                    f"/v1/documents/{doc.id}/compare/",
                    {"profiles": ["fast_text", "structured"]},
                    format="json",
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data["error_code"], "QUEUE_UNAVAILABLE")
            self.assertEqual(IngestionJob.objects.filter(document=doc).count(), 0)
            compare_upload_dir = os.path.join("uploads", "quarantine", str(self.tenant.id))
            compare_paths = []
            for root, _dirs, files in os.walk(os.path.join(tmpdir, compare_upload_dir)):
                compare_paths.extend(files)
            self.assertEqual(compare_paths, [])

    def test_compare_partial_queue_failure_keeps_published_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document(tmpdir)
            calls = {"count": 0}

            def fake_queue(job_id):
                calls["count"] += 1
                if calls["count"] == 1:
                    IngestionJob.objects.filter(pk=job_id).update(celery_task_id="task-queued-1")
                    return None
                raise RuntimeError("broker down")

            with patch(
                "documents.views.start_ingestion_pipeline",
                side_effect=fake_queue,
            ), patch("documents.views.current_app.control.revoke") as revoke_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.id}/compare/",
                    {"profiles": ["fast_text", "structured"]},
                    format="json",
                )

            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.data["error_code"], "PARTIAL_QUEUE_FAILURE")
            remaining_jobs = list(IngestionJob.objects.filter(document=doc))
            self.assertEqual(len(remaining_jobs), 1)
            self.assertEqual(response.data["comparison_id"], str(remaining_jobs[0].comparison_id))
            self.assertEqual(
                response.data["jobs"],
                [{"job_id": remaining_jobs[0].id, "profile": "fast_text"}],
            )
            self.assertEqual(response.data["failed_profiles"], ["structured"])
            self.assertEqual(remaining_jobs[0].profile, "fast_text")
            self.assertEqual(remaining_jobs[0].celery_task_id, "task-queued-1")
            self.assertTrue(os.path.exists(os.path.join(tmpdir, remaining_jobs[0].source_relpath)))
            revoke_mock.assert_not_called()
            compare_upload_dir = os.path.join("uploads", "quarantine", str(self.tenant.id))
            compare_paths = []
            for root, _dirs, files in os.walk(os.path.join(tmpdir, compare_upload_dir)):
                compare_paths.extend(files)
            self.assertEqual(len(compare_paths), 1)


class TestDocumentIngestByUUID(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.raw_key = raw_key
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Primary",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:write", "jobs:read"],
            active=True,
        )
        raw_jobs_key, jobs_prefix, jobs_hash = APIKey.generate_key()
        self.raw_jobs_key = raw_jobs_key
        APIKey.objects.create(
            tenant=self.tenant,
            name="JobsOnly",
            prefix=jobs_prefix,
            key_hash=jobs_hash,
            scopes=["jobs:write"],
            active=True,
        )
        raw_other_key, other_prefix, other_hash = APIKey.generate_key()
        self.raw_other_key = raw_other_key
        self.other_api_key = APIKey.objects.create(
            tenant=self.other_tenant,
            name="OtherPrimary",
            prefix=other_prefix,
            key_hash=other_hash,
            scopes=["documents:write"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

    def _make_document_with_clean(self, tmpdir, tenant=None, api_key=None):
        tenant = tenant or self.tenant
        api_key = api_key or self.api_key
        doc = Document.objects.create(
            tenant=tenant,
            created_by_key=api_key,
            original_filename="sample.pdf",
            sha256=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine=f"uploads/quarantine/{tenant.id}/missing.pdf",
        )
        clean_relpath = os.path.join("uploads", "clean", str(tenant.id), f"{doc.uuid}.pdf")
        clean_abs = os.path.join(tmpdir, clean_relpath)
        os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
        with open(clean_abs, "wb") as handle:
            handle.write(b"%PDF-1.4 clean source\n")
        doc.storage_relpath_clean = clean_relpath
        doc.save(update_fields=["storage_relpath_clean"])
        return doc

    def _make_document_with_quarantine(self, tmpdir):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="pending",
        )
        relpath = os.path.join("uploads", "quarantine", str(self.tenant.id), f"{doc.uuid}.pdf")
        abs_path = os.path.join(tmpdir, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as handle:
            handle.write(b"%PDF-1.4 quarantine source\n")
        doc.storage_relpath_quarantine = relpath
        doc.save(update_fields=["storage_relpath_quarantine"])
        return doc

    def _make_artifact(self, tmpdir, job):
        relpath = os.path.join("artifacts", str(job.tenant_id), str(job.id), "doc.txt")
        abs_path = os.path.join(tmpdir, relpath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as handle:
            handle.write(b"artifact")
        return Artifact.objects.create(
            tenant=job.tenant,
            created_by_key=job.created_by_key,
            job=job,
            kind=ArtifactKind.TEXT,
            storage_relpath=relpath,
            checksum_sha256="c" * 64,
            size_bytes=8,
            content_type="text/plain",
        )

    def _grant_retry_scope(self):
        self.api_key.scopes = ["documents:write", "jobs:read", "jobs:write"]
        self.api_key.save(update_fields=["scopes"])

    def test_ingest_by_uuid_create_new_requires_documents_write(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_jobs_key}")
            response = self.client.post(
                f"/v1/documents/{doc.uuid}/ingest/",
                {"mode": "create_new"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)

    def test_ingest_by_uuid_foreign_document_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_other_key}")
            response = self.client.post(
                f"/v1/documents/{doc.uuid}/ingest/",
                {"mode": "create_new"},
                format="json",
            )

        self.assertEqual(response.status_code, 404)

    def test_ingest_by_uuid_missing_document_returns_404(self):
        response = self.client.post(
            f"/v1/documents/{uuid.uuid4()}/ingest/",
            {"mode": "create_new"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_ingest_by_uuid_create_new_copies_clean_source_and_sets_source_relpath(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "create_new", "profile": "fast_text"},
                    format="json",
                )

            self.assertEqual(response.status_code, 201)
            job = IngestionJob.objects.get(pk=response.data["job_id"])
            start_mock.assert_called_once_with(job.id)
            self.assertTrue(job.source_relpath)
            self.assertNotEqual(job.source_relpath, doc.storage_relpath_clean)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, job.source_relpath)))
            self.assertEqual(job.profile, "fast_text")
            self.assertEqual(job.options_json.get("exports"), ["text", "markdown", "doctags"])

    def test_ingest_by_uuid_create_new_copies_quarantine_source(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_quarantine(tmpdir)
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "create_new"},
                    format="json",
                )

            self.assertEqual(response.status_code, 201)
            job = IngestionJob.objects.get(pk=response.data["job_id"])
            self.assertTrue(job.source_relpath)
            self.assertNotEqual(job.source_relpath, doc.storage_relpath_quarantine)
            with open(os.path.join(tmpdir, job.source_relpath), "rb") as handle:
                self.assertEqual(handle.read(), b"%PDF-1.4 quarantine source\n")

    def test_ingest_by_uuid_reuse_existing_returns_active_job_without_new_job(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.RUNNING,
                stage=IngestionStage.CONVERTING,
                options_json={},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "reuse_existing"},
                    format="json",
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["reused"])
        self.assertEqual(response.data["job_id"], job.id)
        self.assertEqual(IngestionJob.objects.filter(document=doc).count(), 1)
        start_mock.assert_not_called()

    def test_ingest_by_uuid_reuse_existing_returns_succeeded_job_without_new_job(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.SUCCEEDED,
                stage=IngestionStage.FINALIZING,
                options_json={},
            )
            self._make_artifact(tmpdir, job)
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "reuse_existing"},
                    format="json",
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["reused"])
        self.assertEqual(response.data["job_id"], job.id)
        self.assertEqual(response.data["job"]["id"], job.id)
        self.assertEqual(response.data["job"]["uuid"], str(job.uuid))
        self.assertEqual(response.data["job"]["status"], IngestionJobStatus.SUCCEEDED)
        self.assertEqual(response.data["job"]["stage"], IngestionStage.FINALIZING)
        self.assertNotIn("options_json", response.data["job"])
        self.assertNotIn("runtime_json", response.data["job"])
        self.assertNotIn("worker_hostname", response.data["job"])
        self.assertNotIn("celery_task_id", response.data["job"])
        start_mock.assert_not_called()

    def test_ingest_by_uuid_reuse_existing_matches_legacy_profile_options(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.SUCCEEDED,
                stage=IngestionStage.FINALIZING,
                profile="fast_text",
                options_json={"exports": ["text", "markdown", "doctags"]},
            )
            self._make_artifact(tmpdir, job)
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "reuse_existing", "profile": "fast_text"},
                    format="json",
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["reused"])
        self.assertEqual(response.data["job_id"], job.id)
        start_mock.assert_not_called()

    def test_ingest_by_uuid_reuse_existing_creates_job_when_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            with patch("documents.views.start_ingestion_pipeline"):
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "reuse_existing"},
                    format="json",
                )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created"])
        self.assertEqual(IngestionJob.objects.filter(document=doc).count(), 1)

    def test_ingest_by_uuid_reuse_existing_ignores_succeeded_job_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            old_job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.SUCCEEDED,
                stage=IngestionStage.FINALIZING,
                options_json={},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "reuse_existing"},
                    format="json",
                )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created"])
        self.assertNotEqual(response.data["job_id"], old_job.id)
        self.assertEqual(IngestionJob.objects.filter(document=doc).count(), 2)
        start_mock.assert_called_once()

    def test_ingest_by_uuid_retry_failed_requeues_retryable_job(self):
        self._grant_retry_scope()
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.FAILED,
                stage=IngestionStage.EXPORTING,
                attempt=1,
                max_retries=3,
                error_code="DOCLING_CONVERT_FAILED",
                error_message="conversion failed",
                options_json={},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "retry_failed"},
                    format="json",
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["retried"])
            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.QUEUED)
            self.assertEqual(job.stage, IngestionStage.SCANNING)
            self.assertEqual(job.attempt, 2)
            self.assertTrue(job.source_relpath)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, job.source_relpath)))
            start_mock.assert_called_once_with(job.id)

    def test_ingest_by_uuid_retry_failed_matches_legacy_profile_options(self):
        self._grant_retry_scope()
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.FAILED,
                stage=IngestionStage.EXPORTING,
                attempt=1,
                max_retries=3,
                profile="fast_text",
                options_json={"exports": ["text", "markdown", "doctags"]},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "retry_failed", "profile": "fast_text"},
                    format="json",
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["retried"])
            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.QUEUED)
            start_mock.assert_called_once_with(job.id)

    def test_ingest_by_uuid_retry_failed_requires_jobs_write(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.FAILED,
                stage=IngestionStage.EXPORTING,
                attempt=1,
                max_retries=3,
                options_json={},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "retry_failed"},
                    format="json",
                )

        self.assertEqual(response.status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestionJobStatus.FAILED)
        self.assertEqual(job.attempt, 1)
        start_mock.assert_not_called()

    def test_ingest_by_uuid_retry_failed_respects_retry_limit(self):
        self._grant_retry_scope()
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.FAILED,
                stage=IngestionStage.EXPORTING,
                attempt=3,
                max_retries=3,
                options_json={},
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "retry_failed"},
                    format="json",
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "RETRY_LIMIT")
        start_mock.assert_not_called()

    def test_ingest_by_uuid_retry_failed_no_retryable_job_returns_400(self):
        self._grant_retry_scope()
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            response = self.client.post(
                f"/v1/documents/{doc.uuid}/ingest/",
                {"mode": "retry_failed"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "NOT_RETRYABLE")

    def test_ingest_by_uuid_validates_profile_and_options(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            profile_response = self.client.post(
                f"/v1/documents/{doc.uuid}/ingest/",
                {"mode": "create_new", "profile": "unknown"},
                format="json",
            )
            options_response = self.client.post(
                f"/v1/documents/{doc.uuid}/ingest/",
                {"mode": "create_new", "options_json": {"max_num_pages": "ten"}},
                format="json",
            )

        self.assertEqual(profile_response.status_code, 400)
        self.assertEqual(options_response.status_code, 400)
        self.assertEqual(options_response.data["error_code"], "INVALID_OPTIONS")

    def test_ingest_by_uuid_queue_failure_rolls_back_job_and_source_file(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean(tmpdir)
            with patch(
                "documents.views.start_ingestion_pipeline",
                side_effect=RuntimeError("broker down"),
            ):
                response = self.client.post(
                    f"/v1/documents/{doc.uuid}/ingest/",
                    {"mode": "create_new"},
                    format="json",
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(IngestionJob.objects.filter(document=doc).count(), 0)
            quarantine_dir = os.path.join(tmpdir, "uploads", "quarantine", str(self.tenant.id))
            copied_files = []
            if os.path.exists(quarantine_dir):
                for _root, _dirs, files in os.walk(quarantine_dir):
                    copied_files.extend(files)
            self.assertEqual(copied_files, [])
