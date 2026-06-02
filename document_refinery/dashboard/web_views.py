import os
import shutil
import subprocess
import time

from celery import current_app
import json

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import connections
from django.db.models import Count, Q
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from authn.models import APIKey, Tenant
from authn.options import DEFAULT_ALLOWED_UPLOAD_MIME_TYPES
from .runtime import runtime_diagnostics_payload, run_runtime_smoke
from documents.docling_options import capabilities_payload, profile_catalog
from documents.models import (
    Artifact,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)
from documents.validators import validate_webhook_url

@method_decorator(staff_member_required, name="dispatch")
class DashboardPageView(TemplateView):
    template_name = "dashboard/operations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "operations"
        return context


@method_decorator(staff_member_required, name="dispatch")
class DashboardToolsPageView(TemplateView):
    template_name = "dashboard/tools.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "tools"
        context["profiles"] = profile_catalog()
        context["capabilities"] = capabilities_payload()
        return context


@method_decorator(staff_member_required, name="dispatch")
class RuntimeDiagnosticsPageView(TemplateView):
    template_name = "dashboard/runtime.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "runtime"
        context["runtime_payload"] = runtime_diagnostics_payload()
        context["profiles"] = profile_catalog()
        return context


@method_decorator(staff_member_required, name="dispatch")
class DashboardUploadPageView(TemplateView):
    template_name = "dashboard/upload.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "upload"
        context["profiles"] = profile_catalog()
        context["capabilities"] = capabilities_payload()
        return context


@method_decorator(staff_member_required, name="dispatch")
class DashboardJobsPageView(TemplateView):
    template_name = "dashboard/jobs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = (
            IngestionJob.objects.select_related("tenant", "document", "created_by_key")
            .order_by("-created_at", "-id")
        )
        status_filter = (self.request.GET.get("status") or "").strip()
        stage_filter = (self.request.GET.get("stage") or "").strip()
        profile_filter = (self.request.GET.get("profile") or "").strip()
        document_id = (self.request.GET.get("document_id") or "").strip()
        comparison_id = (self.request.GET.get("comparison_id") or "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if stage_filter:
            queryset = queryset.filter(stage=stage_filter)
        if profile_filter:
            queryset = queryset.filter(profile=profile_filter)
        if document_id:
            queryset = queryset.filter(document_id=document_id)
        if comparison_id:
            queryset = queryset.filter(comparison_id=comparison_id)

        context.update(
            {
                "nav_active": "jobs",
                "jobs": queryset[:200],
                "status_filter": status_filter,
                "stage_filter": stage_filter,
                "profile_filter": profile_filter,
                "document_id_filter": document_id,
                "comparison_id_filter": comparison_id,
                "status_choices": IngestionJobStatus.choices,
                "stage_choices": IngestionStage.choices,
                "profiles": profile_catalog(),
            }
        )
        return context


@method_decorator(staff_member_required, name="dispatch")
class DashboardJobDetailPageView(TemplateView):
    template_name = "dashboard/job_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = get_object_or_404(
            IngestionJob.objects.select_related("tenant", "document", "created_by_key"),
            pk=kwargs["pk"],
        )
        artifacts = Artifact.objects.filter(job=job).order_by("kind", "id")
        context.update(
            {
                "nav_active": "jobs",
                "job": job,
                "artifacts": artifacts,
                "options_text": json.dumps(job.options_json or {}, indent=2, sort_keys=True),
                "runtime_text": json.dumps(job.runtime_json or {}, indent=2, sort_keys=True),
                "metrics_text": json.dumps(job.result_metrics_json or {}, indent=2, sort_keys=True),
                "error_details_text": json.dumps(
                    job.error_details_json or {}, indent=2, sort_keys=True
                ),
            }
        )
        return context


@method_decorator(staff_member_required, name="dispatch")
class ProfileComparisonPageView(TemplateView):
    template_name = "dashboard/profile_comparison.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "compare"
        context["profiles"] = profile_catalog()
        context["capabilities"] = capabilities_payload()
        return context


@method_decorator(staff_member_required, name="dispatch")
class DoclingProfilesPageView(TemplateView):
    template_name = "dashboard/profiles.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav_active"] = "profiles"
        context["profiles"] = profile_catalog()
        context["capabilities"] = capabilities_payload()
        return context


_SYSTEM_CACHE: dict[str, object] = {"ts": 0, "payload": None}
_SYSTEM_CACHE_TTL = 5
_SCOPE_LIBRARY = [
    ("dashboard:read", "Dashboard read", "Read dashboard summaries, worker state, and usage reports."),
    ("documents:read", "Documents read", "List uploaded documents and inspect document metadata."),
    ("documents:write", "Documents write", "Upload documents and start comparison runs."),
    ("jobs:read", "Jobs read", "Inspect ingestion job state and comparison job history."),
    ("jobs:write", "Jobs write", "Retry or cancel ingestion jobs."),
    ("artifacts:read", "Artifacts read", "Download generated extraction outputs."),
    ("webhooks:read", "Webhooks read", "Inspect webhook endpoints and delivery history."),
    ("webhooks:write", "Webhooks write", "Create and update webhook endpoints."),
]
_WEBHOOK_EVENT_LIBRARY = [
    ("job.updated", "job.updated", "Send an event whenever an ingestion job changes stage or status."),
]


def _scope_options(selected_scopes: list[str] | None = None) -> list[dict[str, str]]:
    selected = set(selected_scopes or [])
    options = [
        {"value": value, "label": label, "description": description}
        for value, label, description in _SCOPE_LIBRARY
    ]
    known = {value for value, _label, _description in _SCOPE_LIBRARY}
    for value in sorted(selected - known):
        options.append(
            {
                "value": value,
                "label": value,
                "description": "Existing custom scope kept for compatibility.",
            }
        )
    return options


def _webhook_event_options(selected_events: list[str] | None = None) -> list[dict[str, str]]:
    selected = set(selected_events or [])
    options = [
        {"value": value, "label": label, "description": description}
        for value, label, description in _WEBHOOK_EVENT_LIBRARY
    ]
    known = {value for value, _label, _description in _WEBHOOK_EVENT_LIBRARY}
    for value in sorted(selected - known):
        options.append(
            {
                "value": value,
                "label": value,
                "description": "Existing custom event kept for compatibility.",
            }
        )
    return options


def _webhook_tenant_choices() -> list[dict[str, object]]:
    active_key_tenants = set(
        APIKey.objects.filter(active=True).values_list("tenant_id", flat=True)
    )
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "has_active_key": tenant.id in active_key_tenants,
        }
        for tenant in Tenant.objects.order_by("name")
    ]


def _parse_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_request_list(request, name: str) -> list[str]:
    values = []
    for item in request.POST.getlist(name):
        values.extend(_parse_list(item))
    if values:
        return values
    return _parse_list(request.POST.get(name, ""))


def _parse_json(raw: str):
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return json.loads(raw)


def _default_allowed_upload_mime_types_text() -> str:
    return ", ".join(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                value = value.strip().split()[0]
                if value.isdigit():
                    info[key] = int(value) * 1024
    except OSError:
        return {}
    return info


def _read_cpu_model() -> str | None:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _read_uptime() -> int | None:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            raw = handle.read().strip().split()[0]
            return int(float(raw))
    except OSError:
        return None


def _disk_usage(path: str) -> dict[str, int | float]:
    usage = shutil.disk_usage(path)
    percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def _safe_disk_usage(path: str):
    try:
        return _disk_usage(path)
    except OSError:
        return None


def _gpu_info() -> dict[str, object]:
    def _to_int(value: str) -> int | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if normalized.upper() in {"N/A", "[N/A]"}:
            return None
        try:
            return int(normalized)
        except ValueError:
            return None

    info: dict[str, object] = {"available": False}
    driver_version = None
    if os.path.exists("/proc/driver/nvidia/version"):
        try:
            with open("/proc/driver/nvidia/version", "r", encoding="utf-8") as handle:
                driver_version = handle.readline().strip()
        except OSError:
            driver_version = None
    if driver_version:
        info["driver_version"] = driver_version

    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        info["reason"] = "nvidia-smi not installed"
        return info

    cmd = [
        nvidia_smi,
        "--query-gpu=name,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        info["reason"] = f"nvidia-smi failed: {exc}"
        return info
    if result.returncode != 0:
        info["reason"] = result.stderr.strip() or "nvidia-smi failed"
        return info

    gpus = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, mem_total, mem_used, util = parts[:4]
        gpus.append(
            {
                "name": name,
                "memory_total_mb": _to_int(mem_total),
                "memory_used_mb": _to_int(mem_used),
                "utilization_pct": _to_int(util),
            }
        )
    info["available"] = bool(gpus)
    info["gpus"] = gpus
    if not gpus:
        info["reason"] = "no GPUs reported"
    return info


@staff_member_required
def system_status(request):
    now = time.time()
    if _SYSTEM_CACHE["payload"] and now - _SYSTEM_CACHE["ts"] < _SYSTEM_CACHE_TTL:
        return JsonResponse(_SYSTEM_CACHE["payload"])

    checks = {"db": False, "broker": False}
    try:
        connections["default"].cursor()
        checks["db"] = True
    except OperationalError:
        checks["db"] = False
    try:
        current_app.connection().ensure_connection(max_retries=1)
        checks["broker"] = True
    except Exception:
        checks["broker"] = False

    meminfo = _read_meminfo()
    total_mem = meminfo.get("MemTotal")
    avail_mem = meminfo.get("MemAvailable")
    used_mem = total_mem - avail_mem if total_mem is not None and avail_mem is not None else None
    mem_percent = (
        (used_mem / total_mem * 100.0) if used_mem is not None and total_mem else None
    )

    data_root = getattr(settings, "DATA_ROOT", "/var/lib/docling_service")
    loadavg = None
    if hasattr(os, "getloadavg"):
        try:
            loadavg = os.getloadavg()
        except OSError:
            loadavg = None

    metrics_payload = {"jobs": None, "text": "metrics unavailable"}
    if checks.get("db"):
        try:
            status_counts = (
                IngestionJob.objects.values("status")
                .annotate(count=Count("id"))
            )
            counts = {entry["status"]: entry["count"] for entry in status_counts}
            job_counts = {
                "queued": counts.get(IngestionJobStatus.QUEUED, 0),
                "running": counts.get(IngestionJobStatus.RUNNING, 0),
                "failed": counts.get(IngestionJobStatus.FAILED, 0),
                "succeeded": counts.get(IngestionJobStatus.SUCCEEDED, 0),
            }
            metrics_lines = [
                "# HELP docling_jobs_total Total jobs by status.",
                "# TYPE docling_jobs_total gauge",
                f'docling_jobs_total{{status="queued"}} {job_counts["queued"]}',
                f'docling_jobs_total{{status="running"}} {job_counts["running"]}',
                f'docling_jobs_total{{status="failed"}} {job_counts["failed"]}',
                f'docling_jobs_total{{status="succeeded"}} {job_counts["succeeded"]}',
            ]
            metrics_payload = {
                "jobs": job_counts,
                "text": "\n".join(metrics_lines) + "\n",
            }
        except Exception as exc:
            metrics_payload = {
                "jobs": None,
                "text": f"metrics unavailable: {exc.__class__.__name__}",
            }
    else:
        metrics_payload = {
            "jobs": None,
            "text": "metrics unavailable: db down",
        }

    payload = {
        "timestamp": timezone.now().isoformat(),
        "checks": checks,
        "metrics": metrics_payload,
        "cpu": {
            "count": os.cpu_count(),
            "model": _read_cpu_model(),
            "loadavg": loadavg,
        },
        "memory": {
            "total": total_mem,
            "available": avail_mem,
            "used": used_mem,
            "percent": mem_percent,
        },
        "disk": {
            "root": _safe_disk_usage("/"),
            "data_root": _safe_disk_usage(data_root) if os.path.exists(data_root) else None,
        },
        "uptime_seconds": _read_uptime(),
        "gpu": _gpu_info(),
    }

    _SYSTEM_CACHE["payload"] = payload
    _SYSTEM_CACHE["ts"] = now
    return JsonResponse(payload)


@staff_member_required
def runtime_status(request):
    force = request.GET.get("refresh") in {"1", "true", "yes"}
    return JsonResponse(runtime_diagnostics_payload(force_refresh=force))


@staff_member_required
@require_POST
def runtime_smoke(request):
    profile = (request.POST.get("profile") or "fast_text").strip() or "fast_text"
    payload = run_runtime_smoke(profile=profile)
    status_code = 200
    if payload.get("status") == "busy":
        status_code = 409
    elif payload.get("status") == "rate_limited":
        status_code = 429
    elif payload.get("status") == "timeout":
        status_code = 504
    elif payload.get("status") == "fail":
        status_code = 500
    return JsonResponse(payload, status=status_code)


@staff_member_required
def api_keys_list(request):
    keys = APIKey.objects.select_related("tenant").order_by("-created_at")
    return render(
        request,
        "dashboard/api_keys_list.html",
        {"keys": keys, "nav_active": "keys"},
    )


@staff_member_required
def api_key_new(request):
    tenants = Tenant.objects.order_by("name")
    raw_key = None
    errors = None
    selected_scopes: list[str] = []
    allowed_upload_mime_types_text = _default_allowed_upload_mime_types_text()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        tenant_id = request.POST.get("tenant")
        selected_scopes = _parse_request_list(request, "scope_choices") or _parse_list(
            request.POST.get("scopes", "")
        )
        active = request.POST.get("active") == "on"
        options_raw = request.POST.get("docling_options_json", "")
        allowed_upload_mime_types_text = (request.POST.get("allowed_upload_mime_types") or "").strip()
        allowed_upload_mime_types = _parse_list(allowed_upload_mime_types_text)

        if not name or not tenant_id:
            errors = "Tenant and name are required."
        else:
            try:
                tenant = Tenant.objects.get(pk=tenant_id)
                docling_options = _parse_json(options_raw)
                raw_key, prefix, key_hash = APIKey.generate_key()
                APIKey.objects.create(
                    tenant=tenant,
                    name=name,
                    prefix=prefix,
                    key_hash=key_hash,
                    scopes=selected_scopes,
                    active=active,
                    docling_options_json=docling_options,
                    allowed_upload_mime_types=allowed_upload_mime_types,
                )
            except Tenant.DoesNotExist:
                errors = "Selected tenant does not exist."
            except (json.JSONDecodeError, ValidationError) as exc:
                errors = str(exc)

    return render(
        request,
        "dashboard/api_key_new.html",
        {
            "tenants": tenants,
            "raw_key": raw_key,
            "errors": errors,
            "nav_active": "keys",
            "scope_options": _scope_options(selected_scopes),
            "selected_scopes": selected_scopes,
            "allowed_upload_mime_types_text": allowed_upload_mime_types_text,
            "profiles": profile_catalog(),
        },
    )


@staff_member_required
def api_key_detail(request, pk: int):
    key = get_object_or_404(APIKey, pk=pk)
    raw_key = None
    errors = None
    selected_scopes = list(key.scopes or [])
    docling_options_text = (
        json.dumps(key.docling_options_json, indent=2, sort_keys=True)
        if key.docling_options_json
        else ""
    )
    allowed_upload_mime_types_text = ", ".join(
        key.allowed_upload_mime_types or DEFAULT_ALLOWED_UPLOAD_MIME_TYPES
    )

    if request.method == "POST":
        action = request.POST.get("action", "update")
        if action == "rotate":
            raw_key, prefix, key_hash = APIKey.generate_key()
            key.prefix = prefix
            key.key_hash = key_hash
            key.active = True
            key.save()
        else:
            name = (request.POST.get("name") or "").strip()
            selected_scopes = _parse_request_list(request, "scope_choices") or _parse_list(
                request.POST.get("scopes", "")
            )
            active = request.POST.get("active") == "on"
            options_raw = request.POST.get("docling_options_json", "")
            allowed_upload_mime_types_text = (
                request.POST.get("allowed_upload_mime_types") or ""
            ).strip()
            allowed_upload_mime_types = _parse_list(allowed_upload_mime_types_text)
            try:
                key.name = name or key.name
                key.scopes = selected_scopes
                key.active = active
                key.docling_options_json = _parse_json(options_raw)
                key.allowed_upload_mime_types = allowed_upload_mime_types
                key.save()
                docling_options_text = (
                    json.dumps(key.docling_options_json, indent=2, sort_keys=True)
                    if key.docling_options_json
                    else ""
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                errors = str(exc)

    return render(
        request,
        "dashboard/api_key_detail.html",
        {
            "key": key,
            "raw_key": raw_key,
            "errors": errors,
            "nav_active": "keys",
            "scope_options": _scope_options(selected_scopes),
            "selected_scopes": selected_scopes,
            "docling_options_text": docling_options_text,
            "allowed_upload_mime_types_text": allowed_upload_mime_types_text,
            "profiles": profile_catalog(),
        },
    )


@staff_member_required
def webhooks_list(request):
    endpoints = (
        WebhookEndpoint.objects.select_related("tenant")
        .annotate(
            delivery_total=Count("deliveries"),
            delivery_failed=Count(
                "deliveries",
                filter=Q(deliveries__status=WebhookDeliveryStatus.FAILED),
            ),
            delivery_retrying=Count(
                "deliveries",
                filter=Q(deliveries__status=WebhookDeliveryStatus.RETRYING),
            ),
        )
        .order_by("-created_at")
    )
    return render(
        request,
        "dashboard/webhooks_list.html",
        {"endpoints": endpoints, "nav_active": "webhooks"},
    )


@staff_member_required
def webhook_new(request):
    tenants = _webhook_tenant_choices()
    errors = None
    selected_events = ["job.updated"]

    if request.method == "POST":
        tenant_id = request.POST.get("tenant")
        name = (request.POST.get("name") or "").strip()
        url = (request.POST.get("url") or "").strip()
        secret = (request.POST.get("secret") or "").strip()
        selected_events = _parse_request_list(request, "events") or ["job.updated"]
        enabled = request.POST.get("enabled") == "on"

        if not tenant_id or not name or not url:
            errors = "Tenant, name, and URL are required."
        else:
            try:
                validate_webhook_url(url)
                tenant = Tenant.objects.get(pk=tenant_id)
                created_by_key = (
                    APIKey.objects.filter(tenant=tenant, active=True)
                    .order_by("-last_used_at", "-created_at")
                    .first()
                )
                if not created_by_key:
                    errors = "Create an API key for this tenant before adding webhooks."
                else:
                    WebhookEndpoint.objects.create(
                        tenant=tenant,
                        created_by_key=created_by_key,
                        name=name,
                        url=url,
                        secret=secret,
                        events=selected_events,
                        enabled=enabled,
                    )
                    return redirect("/dashboard/webhooks/")
            except Tenant.DoesNotExist:
                errors = "Selected tenant does not exist."
            except ValidationError as exc:
                errors = "; ".join(getattr(exc, "messages", None) or [str(exc)])

    return render(
        request,
        "dashboard/webhook_new.html",
        {
            "tenants": tenants,
            "errors": errors,
            "event_options": _webhook_event_options(selected_events),
            "selected_events": selected_events,
            "nav_active": "webhooks",
        },
    )


@staff_member_required
def webhook_detail(request, pk: int):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    errors = None
    selected_events = list(endpoint.events or ["job.updated"])

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        url = (request.POST.get("url") or "").strip()
        secret = (request.POST.get("secret") or "").strip()
        selected_events = _parse_request_list(request, "events") or ["job.updated"]
        enabled = request.POST.get("enabled") == "on"

        if not name or not url:
            errors = "Name and URL are required."
        else:
            try:
                validate_webhook_url(url)
                endpoint.name = name
                endpoint.url = url
                endpoint.events = selected_events
                endpoint.enabled = enabled
                if secret:
                    endpoint.secret = secret
                endpoint.save()
            except ValidationError as exc:
                errors = "; ".join(getattr(exc, "messages", None) or [str(exc)])

    deliveries = (
        WebhookDelivery.objects.filter(endpoint=endpoint)
        .order_by("-created_at")[:50]
    )

    return render(
        request,
        "dashboard/webhook_detail.html",
        {
            "endpoint": endpoint,
            "deliveries": deliveries,
            "errors": errors,
            "event_options": _webhook_event_options(selected_events),
            "selected_events": selected_events,
            "nav_active": "webhooks",
        },
    )


@staff_member_required
def webhook_deliveries_list(request):
    deliveries = WebhookDelivery.objects.select_related("endpoint").order_by("-created_at")
    endpoints = WebhookEndpoint.objects.order_by("name")
    endpoint_param = (request.GET.get("endpoint") or "").strip()
    selected_endpoint = ""
    if endpoint_param:
        try:
            endpoint_id = int(endpoint_param)
        except ValueError:
            endpoint_id = None
        if endpoint_id and endpoints.filter(pk=endpoint_id).exists():
            deliveries = deliveries.filter(endpoint_id=endpoint_id)
            selected_endpoint = str(endpoint_id)
    status = (request.GET.get("status") or "").strip()
    selected_status = ""
    if status in {value for value, _label in WebhookDeliveryStatus.choices}:
        deliveries = deliveries.filter(status=status)
        selected_status = status
    deliveries = deliveries[:200]
    return render(
        request,
        "dashboard/webhook_deliveries.html",
        {
            "deliveries": deliveries,
            "endpoints": endpoints,
            "selected_endpoint": selected_endpoint,
            "selected_status": selected_status,
            "status_choices": WebhookDeliveryStatus.choices,
            "nav_active": "deliveries",
        },
    )


@staff_member_required
def webhook_delivery_detail(request, pk: int):
    delivery = get_object_or_404(WebhookDelivery.objects.select_related("endpoint"), pk=pk)
    payload = json.dumps(delivery.payload_json or {}, indent=2, sort_keys=True)
    return render(
        request,
        "dashboard/webhook_delivery_detail.html",
        {
            "delivery": delivery,
            "payload": payload,
            "nav_active": "deliveries",
        },
    )
