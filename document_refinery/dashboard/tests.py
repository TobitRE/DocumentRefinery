import os
import tempfile
import time
import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch, mock_open

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from dashboard.models import DashboardActionAudit
from dashboard import web_views
from dashboard.runtime import (
    SMOKE_LOCK_FILENAME,
    SMOKE_RATE_FILENAME,
    run_runtime_smoke,
)
from documents.models import (
    CreationSource,
    Document,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from documents.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint


class TestDashboardSystemView(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )

    def test_system_requires_staff(self):
        response = self.client.get("/dashboard/system")
        self.assertEqual(response.status_code, 302)

    def test_system_returns_payload(self):
        self.client.login(username="staff", password="password")
        response = self.client.get("/dashboard/system")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("cpu", payload)
        self.assertIn("memory", payload)
        self.assertIn("disk", payload)
        self.assertIn("gpu", payload)


class TestDashboardAPI(TestCase):
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
            scopes=["dashboard:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")

        self.doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )

    def test_summary_counts_and_durations(self):
        now = timezone.now()
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SCANNING,
            created_at=now - timedelta(hours=1),
        )
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.RUNNING,
            stage=IngestionStage.CONVERTING,
            created_at=now - timedelta(hours=2),
        )
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            finished_at=now - timedelta(minutes=10),
            duration_ms=1200,
        )
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.FAILED,
            stage=IngestionStage.EXPORTING,
            finished_at=now - timedelta(minutes=5),
            duration_ms=500,
        )

        response = self.client.get("/v1/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["jobs"]["queued"], 1)
        self.assertEqual(payload["jobs"]["running"], 1)
        self.assertEqual(payload["jobs"]["succeeded"], 1)
        self.assertEqual(payload["jobs"]["failed"], 1)
        self.assertIsNotNone(payload["durations_ms"]["avg_24h"])
        self.assertGreaterEqual(len(payload["recent_failures"]), 1)

    def test_workers_view(self):
        class FakeInspect:
            def ping(self):
                return {"worker-1": "pong"}

            def stats(self):
                return {"worker-1": {"pool": {"implementation": "prefork", "max-concurrency": 4}}}

            def active(self):
                return {"worker-1": ["task-1", "task-2"]}

        with patch("dashboard.views.current_app.control.inspect", return_value=FakeInspect()):
            response = self.client.get("/v1/dashboard/workers")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["workers_online"], 1)
        self.assertEqual(payload["workers"][0]["active_tasks"], 2)

    def test_usage_report(self):
        now = timezone.now()
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            finished_at=now - timedelta(days=2),
            duration_ms=100,
        )
        IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=self.doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            finished_at=now - timedelta(hours=2),
            duration_ms=300,
        )
        date_from = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        response = self.client.get(f"/v1/dashboard/reports/usage?from={date_from}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job_count"], 1)
        self.assertEqual(payload["total_duration_ms"], 300)

    def test_usage_report_rejects_invalid_date_filters(self):
        response = self.client.get("/v1/dashboard/reports/usage?from=not-a-date")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error_code"), "INVALID_DATE_FILTER")

    def test_runtime_endpoint_requires_dashboard_read_and_returns_payload(self):
        payload = {
            "summary": {"ok": 1, "warnings": 0, "failures": 0},
            "packages": [],
            "environment": {},
            "filesystem": {},
            "tools": {},
            "ocr_backends": {},
            "celery": {},
        }
        with patch("dashboard.views.runtime_diagnostics_payload", return_value=payload):
            response = self.client.get("/v1/dashboard/runtime")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["ok"], 1)

        raw_key, prefix, key_hash = APIKey.generate_key()
        APIKey.objects.create(
            tenant=self.tenant,
            name="NoDashboardScope",
            prefix=prefix,
            key_hash=key_hash,
            scopes=["documents:read"],
            active=True,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")
        response = self.client.get("/v1/dashboard/runtime")
        self.assertEqual(response.status_code, 403)


@override_settings(WEBHOOK_ALLOWED_HOSTS=["example.com"])
class TestDashboardWebViews(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.login(username="staff", password="password")
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

    def test_dashboard_overview_page(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Operations", content)
        self.assertIn("tabler.min.css", content)
        self.assertIn("/dashboard/upload/", content)

        response = self.client.get("/dashboard/tools/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Dashboard tools", content)
        self.assertIn("/dashboard/upload/", content)
        self.assertIn("/dashboard/runtime/", content)
        self.assertNotIn('id="uploadFile"', content)
        self.assertNotIn("Structured Docling controls", content)

    def test_dashboard_tabler_target_pages(self):
        document = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            size_bytes=10,
            storage_relpath_quarantine="uploads/quarantine/a/a.pdf",
        )
        job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=document,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            profile="fast_text",
            options_json={"max_num_pages": 1},
            runtime_json={"DOCLING_DEVICE": "cpu"},
            result_metrics_json={"page_count": 1},
        )

        checks = [
            ("/dashboard/tools/", "Dashboard tools"),
            ("/dashboard/upload/", "PDF upload"),
            ("/dashboard/jobs/", "Ingestion jobs"),
            (f"/dashboard/jobs/{job.id}/", "Job #"),
            ("/dashboard/compare/", "Compare Docling profiles"),
            ("/dashboard/profiles/", "Profiles and capabilities"),
        ]
        for path, text in checks:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("tabler.min.css", content)
            self.assertIn(text, content)

        response = self.client.get("/dashboard/profiles/")
        content = response.content.decode("utf-8")
        self.assertIn("real_chunking", content)
        self.assertIn("multi_format_upload", content)
        self.assertIn("remote_services", content)

    def test_runtime_page_and_smoke_action(self):
        payload = {
            "summary": {"ok": 3, "warnings": 1, "failures": 0},
            "packages": [
                {
                    "name": "docling",
                    "version": "2.96.1",
                    "expected": "2.96.1",
                    "status": "ok",
                }
            ],
            "environment": {"DOCLING_DEVICE": "cpu"},
            "filesystem": {
                "data_root": {"status": "ok"},
                "hf_home": {"status": "warn"},
            },
            "tools": {
                "ffmpeg": {"status": "warn"},
                "tesseract": {"status": "warn"},
            },
            "ocr_backends": {
                "rapidocr": {"status": "ok"},
                "easyocr": {"status": "warn"},
            },
            "celery": {
                "broker": {"status": "ok"},
                "workers_online": 1,
                "active_tasks": 0,
            },
        }
        with patch("dashboard.web_views.runtime_diagnostics_payload", return_value=payload):
            response = self.client.get("/dashboard/runtime/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Runtime Diagnostics", content)
        self.assertIn("full_vlm (legacy image export, no real VLM)", content)

        with patch(
            "dashboard.web_views.run_runtime_smoke",
            return_value={"status": "ok", "profile": "fast_text", "elapsed_ms": 10},
        ) as smoke:
            response = self.client.post("/dashboard/runtime/smoke", {"profile": "fast_text"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        smoke.assert_called_once_with(profile="fast_text")

    def test_system_status_payload(self):
        mock_broker = MagicMock()
        mock_broker.ensure_connection.return_value = None
        with patch("dashboard.web_views.current_app.connection", return_value=mock_broker), patch(
            "dashboard.web_views._read_meminfo", return_value={"MemTotal": 1024, "MemAvailable": 512}
        ), patch("dashboard.web_views._read_cpu_model", return_value="Test CPU"), patch(
            "dashboard.web_views._read_uptime", return_value=3600
        ), patch(
            "dashboard.web_views._safe_disk_usage",
            return_value={"total": 100, "used": 50, "free": 50, "percent": 50.0},
        ), patch(
            "dashboard.web_views._gpu_info", return_value={"available": False, "reason": "none"}
        ), patch("dashboard.web_views.os.getloadavg", return_value=(0.1, 0.2, 0.3)):
            response = self.client.get("/dashboard/system")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("cpu", payload)
        self.assertIn("memory", payload)
        self.assertIn("metrics", payload)

    def test_api_keys_pages(self):
        response = self.client.get("/dashboard/api-keys/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/dashboard/api-keys/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(
            'data-scopes="dashboard:read,jobs:read,jobs:write,artifacts:read"',
            content,
        )
        self.assertIn(
            'data-scopes="documents:read,documents:write,artifacts:read,jobs:read,jobs:write,webhooks:read,webhooks:write,dashboard:read"',
            content,
        )
        self.assertIn('name="allowed_upload_mime_types"', content)
        self.assertIn('value="application/pdf, application/x-pdf"', content)
        self.assertIn('name="scope_choices"', content)
        self.assertIn("Structured Docling controls", content)
        self.assertIn("JSON fallback", content)

        response = self.client.post(
            "/dashboard/api-keys/new/",
            {
                "tenant": self.tenant.id,
                "name": "Secondary",
                "scopes": "documents:read",
                "allowed_upload_mime_types": "application/pdf, application/x-pdf",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(APIKey.objects.filter(name="Secondary").exists())

        api_key = APIKey.objects.filter(name="Secondary").first()
        self.assertEqual(
            api_key.allowed_upload_mime_types,
            ["application/pdf", "application/x-pdf"],
        )
        response = self.client.get(f"/dashboard/api-keys/{api_key.id}/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(
            'data-scopes="dashboard:read,jobs:read,jobs:write,artifacts:read"',
            content,
        )
        self.assertIn(
            'data-scopes="documents:read,documents:write,artifacts:read,jobs:read,jobs:write,webhooks:read,webhooks:write,dashboard:read"',
            content,
        )
        self.assertIn('name="allowed_upload_mime_types"', content)
        self.assertIn('value="application/pdf, application/x-pdf"', content)
        self.assertIn('name="scope_choices"', content)
        self.assertIn("Structured Docling controls", content)
        self.assertIn("JSON fallback", content)

        response = self.client.post(
            f"/dashboard/api-keys/{api_key.id}/",
            {"action": "rotate"},
        )
        self.assertEqual(response.status_code, 200)

    def test_webhook_pages(self):
        tenant_without_key = Tenant.objects.create(name="Empty", slug="empty")
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            name="Primary",
            url="https://example.com/webhook",
            secret="secret",
            events=["job.updated"],
            enabled=True,
        )
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json={"event": "job.updated"},
            status=WebhookDeliveryStatus.PENDING,
        )

        response = self.client.get("/dashboard/webhooks/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/dashboard/webhooks/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("requires active API key", content)
        self.assertIn(f'value="{tenant_without_key.id}" disabled', content)

        response = self.client.post(
            "/dashboard/webhooks/new/",
            {
                "tenant": self.tenant.id,
                "name": "Secondary",
                "url": "https://example.com/2",
                "events": "job.updated,custom.event",
            },
        )
        self.assertEqual(response.status_code, 302)
        secondary = WebhookEndpoint.objects.get(name="Secondary")
        self.assertEqual(secondary.events, ["job.updated", "custom.event"])

        response = self.client.get(f"/dashboard/webhooks/{endpoint.id}/")
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f"/dashboard/webhooks/{endpoint.id}/",
            {
                "name": "Primary",
                "url": "https://example.com/webhook",
                "events": "job.updated,custom.event",
            },
        )
        self.assertEqual(response.status_code, 200)
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.events, ["job.updated", "custom.event"])

        response = self.client.get("/dashboard/webhook-deliveries/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Apply filters", content)

        response = self.client.get(f"/dashboard/webhook-deliveries/?endpoint={endpoint.id}&status=PENDING")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(endpoint.name, content)
        self.assertIn("selected", content)

        response = self.client.get("/dashboard/webhook-deliveries/?endpoint=abc&status=BOGUS")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Apply filters", content)
        self.assertNotIn("BOGUS", content)

        response = self.client.get(f"/dashboard/webhook-deliveries/{delivery.id}/")
        self.assertEqual(response.status_code, 200)

    def test_api_key_new_invalid_json(self):
        response = self.client.post(
            "/dashboard/api-keys/new/",
            {"tenant": self.tenant.id, "name": "Bad", "docling_options_json": "{bad"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Expecting property name", response.content.decode("utf-8"))

    def test_webhook_new_requires_key(self):
        tenant = Tenant.objects.create(name="Empty", slug="empty")
        response = self.client.post(
            "/dashboard/webhooks/new/",
            {"tenant": tenant.id, "name": "NoKey", "url": "https://example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Create an API key", response.content.decode("utf-8"))


class TestDashboardStaffActions(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.login(username="staff", password="password")
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        raw_key, prefix, key_hash = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            tenant=self.tenant,
            name="Dashboard Test",
            prefix=prefix,
            key_hash=key_hash,
            scopes=[
                "documents:read",
                "documents:write",
                "jobs:read",
                "jobs:write",
                "artifacts:read",
                "dashboard:read",
            ],
            active=True,
            is_dashboard_test_key=True,
        )

    def _make_document_with_clean_source(self, tmpdir, content=b"%PDF-1.4 clean\n"):
        doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="sample.pdf",
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_relpath_quarantine=f"uploads/quarantine/{self.tenant.id}/missing.pdf",
        )
        clean_relpath = os.path.join("uploads", "clean", str(self.tenant.id), f"{doc.uuid}.pdf")
        clean_abs = os.path.join(tmpdir, clean_relpath)
        os.makedirs(os.path.dirname(clean_abs), exist_ok=True)
        with open(clean_abs, "wb") as handle:
            handle.write(content)
        doc.storage_relpath_clean = clean_relpath
        doc.save(update_fields=["storage_relpath_clean"])
        return doc

    def test_dashboard_context_prefers_test_key(self):
        response = self.client.get("/dashboard/api/context")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["default_key_id"], self.api_key.id)
        self.assertTrue(payload["keys"][0]["is_dashboard_test_key"])
        self.assertEqual(payload["keys"][0]["dashboard_billable_actions_30d"], 0)

    def test_dashboard_context_includes_tenant_billable_action_summary(self):
        DashboardActionAudit.objects.create(
            tenant=self.tenant,
            api_key=self.api_key,
            created_by_user=self.user,
            action=DashboardActionAudit.Action.DOCUMENT_INGEST,
            potentially_billable=True,
            tenant_name=self.tenant.name,
            api_key_name=self.api_key.name,
            api_key_prefix=self.api_key.prefix,
        )

        response = self.client.get("/dashboard/api/context")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["keys"][0]["dashboard_billable_actions_30d"], 1)
        self.assertIsNotNone(payload["keys"][0]["dashboard_billable_last_at"])

    def test_dashboard_documents_lists_selected_tenant_documents(self):
        self.api_key.last_used_at = None
        self.api_key.save(update_fields=["last_used_at"])
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean_source(tmpdir)
            response = self.client.get(f"/dashboard/api/documents/?api_key_id={self.api_key.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["documents"][0]["id"], doc.id)
        self.assertEqual(payload["documents"][0]["job_count"], 0)
        self.assertEqual(payload["documents"][0]["created_via"], CreationSource.API)
        self.api_key.refresh_from_db()
        self.assertIsNone(self.api_key.last_used_at)

    def test_dashboard_upload_duplicate_returns_existing_document_by_default(self):
        content = b"%PDF-1.4 duplicate\n"
        existing_doc = Document.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            original_filename="existing.pdf",
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_relpath_quarantine="uploads/quarantine/existing.pdf",
        )
        latest_job = IngestionJob.objects.create(
            tenant=self.tenant,
            created_by_key=self.api_key,
            document=existing_doc,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
        )
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            upload = SimpleUploadedFile("sample.pdf", content, content_type="application/pdf")
            response = self.client.post(
                "/dashboard/api/documents/",
                {"api_key_id": self.api_key.id, "file": upload},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["document"]["id"], existing_doc.id)
        self.assertEqual(payload["latest_job"]["id"], latest_job.id)
        audit = DashboardActionAudit.objects.get(
            action=DashboardActionAudit.Action.DOCUMENT_DUPLICATE_REUSE
        )
        self.assertEqual(audit.document_id, existing_doc.id)
        self.assertFalse(audit.potentially_billable)
        self.assertEqual(audit.created_by_user_id, self.user.id)

    def test_dashboard_existing_document_can_create_new_job(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean_source(tmpdir)
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/dashboard/api/documents/{doc.uuid}/ingest/",
                    data=json.dumps(
                        {
                            "api_key_id": self.api_key.id,
                            "mode": "create_new",
                            "profile": "fast_text",
                        }
                    ),
                    content_type="application/json",
                )

            self.assertEqual(response.status_code, 201)
            payload = response.json()
            job = IngestionJob.objects.get(pk=payload["job_id"])
            self.assertEqual(job.document_id, doc.id)
            self.assertEqual(job.profile, "fast_text")
            self.assertTrue(job.source_relpath)
            self.assertEqual(job.created_via, CreationSource.DASHBOARD)
            self.assertEqual(job.created_by_user_id, self.user.id)
            audit = DashboardActionAudit.objects.get(
                action=DashboardActionAudit.Action.DOCUMENT_INGEST
            )
            self.assertEqual(audit.document_id, doc.id)
            self.assertEqual(audit.job_id, job.id)
            self.assertTrue(audit.potentially_billable)
            start_mock.assert_called_once_with(job.id)

    def test_dashboard_failed_job_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean_source(tmpdir)
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
            )
            with patch("documents.views.start_ingestion_pipeline") as start_mock:
                response = self.client.post(
                    f"/dashboard/api/jobs/{job.id}/retry/",
                    data=json.dumps({"api_key_id": self.api_key.id}),
                    content_type="application/json",
                )

            self.assertEqual(response.status_code, 200)
            job.refresh_from_db()
            self.assertEqual(job.status, IngestionJobStatus.QUEUED)
            self.assertEqual(job.stage, IngestionStage.SCANNING)
            self.assertEqual(job.attempt, 2)
            self.assertEqual(job.dashboard_last_action_by_id, self.user.id)
            self.assertIsNotNone(job.dashboard_last_action_at)
            audit = DashboardActionAudit.objects.get(action=DashboardActionAudit.Action.JOB_RETRY)
            self.assertEqual(audit.job_id, job.id)
            self.assertTrue(audit.potentially_billable)
            start_mock.assert_called_once_with(job.id)

    def test_dashboard_jobs_uses_selected_key_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            doc = self._make_document_with_clean_source(tmpdir)
            job = IngestionJob.objects.create(
                tenant=self.tenant,
                created_by_key=self.api_key,
                document=doc,
                status=IngestionJobStatus.SUCCEEDED,
                stage=IngestionStage.FINALIZING,
            )
            response = self.client.get(
                f"/dashboard/api/jobs/?api_key_id={self.api_key.id}&document_id={doc.id}"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["jobs"][0]["id"], job.id)
        self.assertEqual(payload["jobs"][0]["created_via"], CreationSource.API)


class TestRuntimeSmokeGuards(TestCase):
    def _state_path(self, tmpdir: str, filename: str) -> str:
        state_dir = os.path.join(tmpdir, "runtime")
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, filename)

    def test_runtime_smoke_uses_shared_file_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            with open(self._state_path(tmpdir, SMOKE_LOCK_FILENAME), "w", encoding="utf-8") as handle:
                handle.write(str(time.time()))

            payload = run_runtime_smoke()

        self.assertEqual(payload["status"], "busy")

    def test_runtime_smoke_uses_shared_file_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(DATA_ROOT=tmpdir):
            with open(self._state_path(tmpdir, SMOKE_RATE_FILENAME), "w", encoding="utf-8") as handle:
                handle.write(str(time.time()))

            payload = run_runtime_smoke()

        self.assertEqual(payload["status"], "rate_limited")


class TestDashboardWebHelpers(TestCase):
    def test_read_meminfo_parses(self):
        content = "MemTotal:       1000 kB\nMemAvailable:  500 kB\n"
        with patch("builtins.open", mock_open(read_data=content)):
            data = web_views._read_meminfo()
        self.assertEqual(data.get("MemTotal"), 1000 * 1024)
        self.assertEqual(data.get("MemAvailable"), 500 * 1024)

    def test_read_meminfo_handles_error(self):
        with patch("builtins.open", side_effect=OSError):
            data = web_views._read_meminfo()
        self.assertEqual(data, {})

    def test_read_cpu_model(self):
        content = "model name\t: Test CPU\n"
        with patch("builtins.open", mock_open(read_data=content)):
            model = web_views._read_cpu_model()
        self.assertEqual(model, "Test CPU")

        with patch("builtins.open", side_effect=OSError):
            model = web_views._read_cpu_model()
        self.assertIsNone(model)

    def test_read_uptime(self):
        with patch("builtins.open", mock_open(read_data="123.45 0.00\n")):
            uptime = web_views._read_uptime()
        self.assertEqual(uptime, 123)

        with patch("builtins.open", side_effect=OSError):
            uptime = web_views._read_uptime()
        self.assertIsNone(uptime)

    def test_disk_usage_helpers(self):
        usage = web_views._disk_usage("/")
        self.assertIn("total", usage)
        self.assertIn("used", usage)
        self.assertIn("percent", usage)

        with patch("dashboard.web_views.shutil.disk_usage", side_effect=OSError):
            self.assertIsNone(web_views._safe_disk_usage("/missing"))

    def test_gpu_info_no_nvidia(self):
        with patch("dashboard.web_views.os.path.exists", return_value=False), patch(
            "dashboard.web_views.shutil.which", return_value=None
        ):
            info = web_views._gpu_info()
        self.assertFalse(info["available"])
        self.assertIn("reason", info)

    def test_gpu_info_with_data(self):
        fake_run = MagicMock()
        fake_run.returncode = 0
        fake_run.stdout = "Fake GPU, 10000, 5000, 10\n"
        with patch("dashboard.web_views.os.path.exists", return_value=True), patch(
            "builtins.open", mock_open(read_data="Driver Version: 1.0\n")
        ), patch("dashboard.web_views.shutil.which", return_value="nvidia-smi"), patch(
            "dashboard.web_views.subprocess.run", return_value=fake_run
        ):
            info = web_views._gpu_info()
        self.assertTrue(info["available"])
        self.assertEqual(info["gpus"][0]["name"], "Fake GPU")

    def test_gpu_info_with_non_numeric_values(self):
        fake_run = MagicMock()
        fake_run.returncode = 0
        fake_run.stdout = "Fake GPU, 10000, N/A, N/A\n"
        with patch("dashboard.web_views.os.path.exists", return_value=True), patch(
            "builtins.open", mock_open(read_data="Driver Version: 1.0\n")
        ), patch("dashboard.web_views.shutil.which", return_value="nvidia-smi"), patch(
            "dashboard.web_views.subprocess.run", return_value=fake_run
        ):
            info = web_views._gpu_info()
        self.assertTrue(info["available"])
        self.assertEqual(info["gpus"][0]["name"], "Fake GPU")
        self.assertIsNone(info["gpus"][0]["memory_used_mb"])
        self.assertIsNone(info["gpus"][0]["utilization_pct"])
