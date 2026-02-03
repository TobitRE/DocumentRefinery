import hashlib
import os
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from authn.models import APIKey, Tenant
from documents.models import Document


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
            self.assertTrue(os.path.exists(doc.get_quarantine_path()))

    def test_upload_rejects_large_file(self):
        content = b"x" * (1024 * 1024 + 1)
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

    def test_upload_streamed_file_too_large(self):
        content = b"x" * (1024 * 1024 + 5)
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
        Document.objects.create(
            tenant=self.tenant,
            created_by_key=APIKey.objects.get(tenant=self.tenant),
            original_filename="existing.pdf",
            sha256=existing_hash,
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_relpath_quarantine="uploads/quarantine/existing.pdf",
        )
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            self._auth()
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post("/v1/documents/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, 409)

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
