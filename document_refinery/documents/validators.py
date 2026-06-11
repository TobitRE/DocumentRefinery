import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError


_BLOCKED_HOSTS = {"localhost"}
_BLOCKED_SUFFIXES = (".local", ".localhost")


def _host_in_allowlist(host: str) -> bool:
    allowed = getattr(settings, "WEBHOOK_ALLOWED_HOSTS", []) or []
    host = host.lower()
    for entry in allowed:
        entry = str(entry).strip().lower()
        if not entry:
            continue
        if host == entry or host.endswith(f".{entry}"):
            return True
    return False


def _validate_resolved_hosts(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        raise ValidationError("Webhook URL host could not be resolved.") from exc

    if not infos:
        raise ValidationError("Webhook URL host could not be resolved.")

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise ValidationError("Webhook URL host could not be resolved.") from exc
        if not ip.is_global:
            raise ValidationError("Webhook URL resolves to a private or local address.")


def _validated_port(parsed) -> int:
    try:
        return parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationError("Webhook URL must include a valid port.") from exc


def validate_webhook_url(url: str) -> None:
    if not url:
        return

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Webhook URL must start with http:// or https://.")
    if not parsed.hostname:
        raise ValidationError("Webhook URL must include a valid host.")
    if parsed.username or parsed.password:
        raise ValidationError("Webhook URL must not include credentials.")

    host = parsed.hostname.strip().lower().rstrip(".")
    if host in _BLOCKED_HOSTS or any(host.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise ValidationError("Webhook URL host is not allowed.")
    port = _validated_port(parsed)

    if _host_in_allowlist(host):
        return

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        _validate_resolved_hosts(host, port)
        return

    if not ip.is_global:
        raise ValidationError("Webhook URL must not target private or local addresses.")
