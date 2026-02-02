import os
import shutil
import subprocess
import time

from celery import current_app
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connections
from django.db.models import Count
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from documents.models import IngestionJob, IngestionJobStatus

@method_decorator(staff_member_required, name="dispatch")
class DashboardPageView(TemplateView):
    template_name = "dashboard/index.html"


_SYSTEM_CACHE: dict[str, object] = {"ts": 0, "payload": None}
_SYSTEM_CACHE_TTL = 5


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
                "memory_total_mb": int(mem_total),
                "memory_used_mb": int(mem_used),
                "utilization_pct": int(util),
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
