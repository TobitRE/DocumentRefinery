import socket
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from documents.validators import validate_webhook_url


class TestWebhookURLValidator(TestCase):
    def test_empty_url_is_allowed_for_optional_model_field_validation(self):
        validate_webhook_url("")

    def test_rejects_missing_host(self):
        with self.assertRaisesMessage(ValidationError, "valid host"):
            validate_webhook_url("https:///payload")

    def test_rejects_malformed_ports_as_validation_errors(self):
        for url in ("https://example.com:bad/hook", "https://example.com:99999/hook"):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValidationError, "valid port"):
                    validate_webhook_url(url)

    @override_settings(WEBHOOK_ALLOWED_HOSTS=["example.com"])
    def test_allowlisted_hosts_still_require_valid_ports(self):
        with self.assertRaisesMessage(ValidationError, "valid port"):
            validate_webhook_url("https://example.com:bad/hook")

    def test_rejects_credentials_and_local_names(self):
        cases = [
            ("https://user:pass@example.com/hook", "credentials"),
            ("https://localhost/hook", "not allowed"),
            ("https://service.local/hook", "not allowed"),
            ("ftp://example.com/hook", "http:// or https://"),
        ]
        for url, message in cases:
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValidationError, message):
                    validate_webhook_url(url)

    def test_rejects_private_addresses_from_dns_resolution(self):
        resolved = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.0.0.5", 443),
            )
        ]
        with patch("documents.validators.socket.getaddrinfo", return_value=resolved):
            with self.assertRaisesMessage(ValidationError, "private or local"):
                validate_webhook_url("https://hooks.example.test/payload")

    def test_rejects_dns_resolution_failures(self):
        with patch(
            "documents.validators.socket.getaddrinfo",
            side_effect=socket.gaierror,
        ):
            with self.assertRaisesMessage(ValidationError, "could not be resolved"):
                validate_webhook_url("https://hooks.example.test/payload")

        with patch("documents.validators.socket.getaddrinfo", return_value=[]):
            with self.assertRaisesMessage(ValidationError, "could not be resolved"):
                validate_webhook_url("https://hooks.example.test/payload")

        resolved = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("not-an-ip", 443),
            )
        ]
        with patch("documents.validators.socket.getaddrinfo", return_value=resolved):
            with self.assertRaisesMessage(ValidationError, "could not be resolved"):
                validate_webhook_url("https://hooks.example.test/payload")

    def test_accepts_global_addresses_from_dns_resolution(self):
        resolved = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]
        with patch("documents.validators.socket.getaddrinfo", return_value=resolved):
            validate_webhook_url("https://hooks.example.test/payload")

    @override_settings(WEBHOOK_ALLOWED_HOSTS=["example.com"])
    def test_allowlist_accepts_subdomains_without_dns_lookup(self):
        with patch("documents.validators.socket.getaddrinfo") as getaddrinfo:
            validate_webhook_url("https://hooks.example.com/payload")
        getaddrinfo.assert_not_called()

    @override_settings(WEBHOOK_ALLOWED_HOSTS=["", "example.com"])
    def test_allowlist_ignores_empty_entries(self):
        with patch("documents.validators.socket.getaddrinfo") as getaddrinfo:
            validate_webhook_url("https://example.com/payload")
        getaddrinfo.assert_not_called()
