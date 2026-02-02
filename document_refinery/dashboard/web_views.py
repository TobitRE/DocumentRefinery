import os
import shutil
import subprocess
import time

from celery import current_app
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView


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
    payload = {
        "timestamp": timezone.now().isoformat(),
        "checks": checks,
        "cpu": {
            "count": os.cpu_count(),
            "model": _read_cpu_model(),
            "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None,
        },
        "memory": {
            "total": total_mem,
            "available": avail_mem,
            "used": used_mem,
            "percent": mem_percent,
        },
        "disk": {
            "root": _disk_usage("/"),
            "data_root": _disk_usage(data_root) if os.path.exists(data_root) else None,
        },
        "uptime_seconds": _read_uptime(),
        "gpu": _gpu_info(),
    }

    _SYSTEM_CACHE["payload"] = payload
    _SYSTEM_CACHE["ts"] = now
    return JsonResponse(payload)
