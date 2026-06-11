from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def _retention_days(tenant, tenant_field: str, setting_name: str) -> int:
    tenant_value = getattr(tenant, tenant_field, None)
    if tenant_value is not None:
        return int(tenant_value)
    return int(getattr(settings, setting_name, 0) or 0)


def _expires_at_for_days(days: int):
    if days <= 0:
        return None
    return timezone.now() + timedelta(days=days)


def document_retention_days(tenant) -> int:
    return _retention_days(
        tenant,
        "document_retention_days",
        "DOCUMENT_RETENTION_DAYS",
    )


def artifact_retention_days(tenant) -> int:
    return _retention_days(
        tenant,
        "artifact_retention_days",
        "ARTIFACT_RETENTION_DAYS",
    )


def infected_quarantine_retention_days(tenant) -> int:
    return _retention_days(
        tenant,
        "infected_quarantine_retention_days",
        "INFECTED_QUARANTINE_RETENTION_DAYS",
    )


def document_expires_at(tenant):
    return _expires_at_for_days(document_retention_days(tenant))


def artifact_expires_at(tenant):
    return _expires_at_for_days(artifact_retention_days(tenant))
