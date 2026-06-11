import os
import json
import tempfile
import zipfile

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

    def test_text_preview_returns_limited_text(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "doc.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"hello preview")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=13,
                content_type="text/plain",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["preview_type"], "text")
            self.assertEqual(response.data["text"], "hello preview")
            self.assertFalse(response.data["truncated"])

    def test_json_preview_parses_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "doc.json")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(json.dumps({"pages": 1}).encode("utf-8"))
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.DOCLING_JSON,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=12,
                content_type="application/json",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["preview_type"], "json")
            self.assertEqual(response.data["json"], {"pages": 1})

    def test_chunks_preview_marks_compatibility_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "chunks.json")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(json.dumps({"format": "doctags", "content": "x"}).encode("utf-8"))
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.CHUNKS_JSON,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=36,
                content_type="application/json",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["preview_type"], "json")
            self.assertIn("not real chunking", response.data["compatibility_note"])

    def test_zip_preview_returns_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "figures.zip")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with zipfile.ZipFile(abs_path, "w") as archive:
                archive.writestr("figure_1.png", b"fake")
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.FIGURES_ZIP,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=os.path.getsize(abs_path),
                content_type="application/zip",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["preview_type"], "zip_metadata")
            self.assertEqual(response.data["entry_count"], 1)
            self.assertEqual(response.data["entries"][0]["filename"], "figure_1.png")

    def test_preview_truncates_large_text(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            relpath = os.path.join("artifacts", str(self.tenant.id), str(self.job.id), "large.txt")
            abs_path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as handle:
                handle.write(b"a" * (256 * 1024 + 10))
            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath=relpath,
                checksum_sha256="c" * 64,
                size_bytes=256 * 1024 + 10,
                content_type="text/plain",
            )
            self._auth(self.raw_key)
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["truncated"])
            self.assertEqual(len(response.data["text"]), 256 * 1024)

    def test_preview_wrong_tenant_returns_404(self):
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
            response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
            self.assertEqual(response.status_code, 404)

    def test_artifact_list_does_not_expose_internal_storage_path(self):
        Artifact.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            job=self.job,
            kind=ArtifactKind.TEXT,
            storage_relpath=os.path.join(
                "artifacts", str(self.tenant.id), str(self.job.id), "doc.txt"
            ),
            checksum_sha256="c" * 64,
            size_bytes=5,
            content_type="text/plain",
        )

        self._auth(self.raw_key)
        response = self.client.get("/v1/artifacts/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("storage_relpath", response.data[0])

    def test_artifact_download_and_preview_reject_path_traversal_relpath(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = os.path.join(tmpdir, "data")
            os.makedirs(data_root, exist_ok=True)
            outside_path = os.path.join(tmpdir, "secret.txt")
            with open(outside_path, "wb") as handle:
                handle.write(b"outside data root")

            artifact = Artifact.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                job=self.job,
                kind=ArtifactKind.TEXT,
                storage_relpath="../secret.txt",
                checksum_sha256="c" * 64,
                size_bytes=17,
                content_type="text/plain",
            )

            with override_settings(DATA_ROOT=data_root, X_ACCEL_REDIRECT_ENABLED=True):
                self._auth(self.raw_key)
                response = self.client.get(f"/v1/artifacts/{artifact.id}/")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("X-Accel-Redirect", response)

                response = self.client.get(f"/v1/artifacts/{artifact.id}/preview/")
                self.assertEqual(response.status_code, 404)
