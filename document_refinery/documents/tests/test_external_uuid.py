import tempfile
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import Document, IngestionJob


class TestExternalUUID(TestCase):
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

    def _upload_pdf(self, external_uuid: uuid.UUID, ingest: bool = False):
        pdf_bytes = b"%PDF-1.4 test\n"
        upload = SimpleUploadedFile("sample.pdf", pdf_bytes, content_type="application/pdf")
        data = {
            "file": upload,
            "external_uuid": str(external_uuid),
        }
        if ingest:
            data["ingest"] = "true"
        return self.client.post("/v1/documents/", data, format="multipart")

    def test_upload_persists_external_uuid(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            external_uuid = uuid.uuid4()
            response = self._upload_pdf(external_uuid)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["external_uuid"], str(external_uuid))
        doc = Document.objects.get(pk=response.data["id"])
        self.assertEqual(str(doc.external_uuid), str(external_uuid))

    def test_job_echoes_external_uuid(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            external_uuid = uuid.uuid4()
            with patch("documents.views.start_ingestion_pipeline"):
                response = self._upload_pdf(external_uuid, ingest=True)

        self.assertEqual(response.status_code, 201)
        job_id = response.data.get("job_id")
        self.assertIsNotNone(job_id)
        job = IngestionJob.objects.get(pk=job_id)
        self.assertEqual(str(job.external_uuid), str(external_uuid))
        job_response = self.client.get(f"/v1/jobs/{job_id}/")
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data["external_uuid"], str(external_uuid))
