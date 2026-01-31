from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db import connections
from django.db.utils import OperationalError
from celery import current_app
from django.db.models import Count

from documents.models import IngestionJob, IngestionJobStatus


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


def metrics(request):
    queued = IngestionJob.objects.filter(status=IngestionJobStatus.QUEUED).count()
    running = IngestionJob.objects.filter(status=IngestionJobStatus.RUNNING).count()
    failed = IngestionJob.objects.filter(status=IngestionJobStatus.FAILED).count()
    succeeded = IngestionJob.objects.filter(status=IngestionJobStatus.SUCCEEDED).count()

    lines = [
        "# HELP docling_jobs_total Total jobs by status.",
        "# TYPE docling_jobs_total gauge",
        f'docling_jobs_total{{status="queued"}} {queued}',
        f'docling_jobs_total{{status="running"}} {running}',
        f'docling_jobs_total{{status="failed"}} {failed}',
        f'docling_jobs_total{{status="succeeded"}} {succeeded}',
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")

# Create your views here.
