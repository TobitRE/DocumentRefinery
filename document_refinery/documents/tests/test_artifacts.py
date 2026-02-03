import os
import tempfile

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from documents.models import Artifact, ArtifactKind, Document, IngestionJob, IngestionStage, IngestionJobStatus


class TestArtifactAccess(TestCase):
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
            scopes=["artifacts:read"],
            active=True,
        )
        raw_key_other, prefix_other, key_hash_other = APIKey.generate_key()
        self.raw_key_other = raw_key_other
        self.api_key_other = APIKey.objects.create(
            tenant=self.other_tenant,
            name="Other",
            prefix=prefix_other,
            key_hash=key_hash_other,
            scopes=["artifacts:read"],
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
        self.job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.EXPORTING,
        )

    def _auth(self, raw_key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")

    def test_artifact_download_file_response(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir, X_ACCEL_REDIRECT_ENABLED=False
        ):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "doc.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"hello")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=5,
                content_type="text/plain",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/")
            self.assertEqual(response.status_code, 200)

    def test_artifact_download_x_accel(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            DATA_ROOT=tmpdir,
            X_ACCEL_REDIRECT_ENABLED=True,
            X_ACCEL_REDIRECT_LOCATION="/protected",
        ):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "doc.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"hello")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=5,
                content_type="text/plain",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("X-Accel-Redirect", response)

    def test_artifact_missing_file_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "missing.txt")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=5,
                content_type="text/plain",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/")
            self.assertEqual(response.status_code, 404)

    def test_artifact_wrong_tenant_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "doc.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"hello")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=5,
                content_type="text/plain",
            )
            self._auth(self.raw_key_other)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/")
            self.assertEqual(response.status_code, 404)
