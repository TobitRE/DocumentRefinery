from datetime import timedelta
from unittest.mock import MagicMock, patch, mock_open

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authn.models import APIKey, Tenant
from dashboard import web_views
from documents.models import Document, IngestionJob, IngestionJobStatus, IngestionStage
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

        response = self.client.post(
            "/dashboard/api-keys/new/",
            {"tenant": self.tenant.id, "name": "Secondary", "scopes": "documents:read"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(APIKey.objects.filter(name="Secondary").exists())

        api_key = APIKey.objects.filter(name="Secondary").first()
        response = self.client.get(f"/dashboard/api-keys/{api_key.id}/")
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f"/dashboard/api-keys/{api_key.id}/",
            {"action": "rotate"},
        )
        self.assertEqual(response.status_code, 200)

    def test_webhook_pages(self):
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

        response = self.client.post(
            "/dashboard/webhooks/new/",
            {"tenant": self.tenant.id, "name": "Secondary", "url": "https://example.com/2"},
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get(f"/dashboard/webhooks/{endpoint.id}/")
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f"/dashboard/webhooks/{endpoint.id}/",
            {"name": "Primary", "url": "https://example.com/webhook", "events": "job.updated"},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/dashboard/webhook-deliveries/")
        self.assertEqual(response.status_code, 200)

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
