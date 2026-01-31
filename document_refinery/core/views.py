from django.http import JsonResponse
from django.utils import timezone
from django.db import connections
from django.db.utils import OperationalError
from celery import current_app


def healthz(request):
    return JsonResponse(
        {"status": "ok", "timestamp": timezone.now().isoformat()},
        status=200,
    )


def readyz(request):
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

    ok = all(checks.values())
    status = "ok" if ok else "degraded"
    return JsonResponse(
        {"status": status, "checks": checks, "timestamp": timezone.now().isoformat()},
        status=200 if ok else 503,
    )

# Create your views here.
