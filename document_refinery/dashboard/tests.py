from django.contrib.auth import get_user_model
from django.test import TestCase

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
