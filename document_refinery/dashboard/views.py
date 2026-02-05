import math
import time

from celery import current_app
from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from authn.permissions import APIKeyRequired, HasScope
from documents.models import IngestionJob, IngestionJobStatus


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    values_sorted = sorted(values)
    k = max(0, min(len(values_sorted) - 1, math.ceil(percentile * len(values_sorted)) - 1))
    return int(values_sorted[k])


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    values_sorted = sorted(values)
    n = len(values_sorted)
    mid = n // 2
    if n % 2:
        return int(values_sorted[mid])
    return int((values_sorted[mid - 1] + values_sorted[mid]) / 2)


_WORKER_CACHE: dict[str, object] = {"ts": 0, "payload": None}
_WORKER_CACHE_TTL = 5


class DashboardSummaryView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes = ["dashboard:read"]

    def get(self, request):
        api_key = request.auth
        jobs = IngestionJob.objects.filter(tenant=api_key.tenant)

        status_counts = {s: 0 for s in IngestionJobStatus.values}
        for row in jobs.values("status").annotate(count=Count("id")):
            status_counts[row["status"]] = row["count"]

        stages_running = {}
        for row in jobs.filter(status=IngestionJobStatus.RUNNING).values("stage").annotate(
            count=Count("id")
        ):
            stages_running[row["stage"]] = row["count"]

        now = timezone.now()
        since_24h = now - timezone.timedelta(hours=24)
        since_7d = now - timezone.timedelta(days=7)
        since_30d = now - timezone.timedelta(days=30)

        finished_24h = jobs.filter(finished_at__gte=since_24h, duration_ms__isnull=False)
        durations_24h = list(
            finished_24h.values_list("duration_ms", flat=True).order_by("duration_ms")
        )
        duration_stats = finished_24h.aggregate(
            avg=Avg("duration_ms"),
            total=Sum("duration_ms"),
        )

        recent_failures = list(
            jobs.filter(status__in=[IngestionJobStatus.FAILED, IngestionJobStatus.QUARANTINED])
            .order_by("-finished_at")[:10]
            .values(
                "id",
                "document_id",
                "comparison_id",
                "profile",
                "status",
                "error_code",
                "error_message",
                "stage",
                "attempt",
                "max_retries",
                "finished_at",
            )
        )

        payload = {
            "jobs": {
                "queued": status_counts.get(IngestionJobStatus.QUEUED, 0),
                "running": status_counts.get(IngestionJobStatus.RUNNING, 0),
                "succeeded": status_counts.get(IngestionJobStatus.SUCCEEDED, 0),
                "failed": status_counts.get(IngestionJobStatus.FAILED, 0),
                "canceled": status_counts.get(IngestionJobStatus.CANCELED, 0),
                "quarantined": status_counts.get(IngestionJobStatus.QUARANTINED, 0),
            },
            "stages_running": stages_running,
            "durations_ms": {
                "avg_24h": int(duration_stats["avg"]) if duration_stats["avg"] else None,
                "p50_24h": _median(durations_24h),
                "p95_24h": _percentile(durations_24h, 0.95),
                "total_24h": int(duration_stats["total"]) if duration_stats["total"] else None,
                "total_30d": int(
                    jobs.filter(finished_at__gte=since_30d).aggregate(total=Sum("duration_ms"))[
                        "total"
                    ]
                    or 0
                ),
            },
            "recent_failures": recent_failures,
            "throughput": {
                "jobs_24h": jobs.filter(created_at__gte=since_24h).count(),
                "jobs_7d": jobs.filter(created_at__gte=since_7d).count(),
            },
        }
        return Response(payload)


class DashboardWorkersView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes = ["dashboard:read"]

    def get(self, request):
        now = time.time()
        if _WORKER_CACHE["payload"] and now - _WORKER_CACHE["ts"] < _WORKER_CACHE_TTL:
            return Response(_WORKER_CACHE["payload"])

        try:
            inspect = current_app.control.inspect()
            ping = inspect.ping() or {}
            stats = inspect.stats() or {}
            active = inspect.active() or {}
        except Exception:
            ping, stats, active = {}, {}, {}

        workers = []
        for hostname, info in (stats or {}).items():
            workers.append(
                {
                    "hostname": hostname,
                    "active_tasks": len((active or {}).get(hostname, [])),
                    "pool": (info.get("pool") or {}).get("implementation"),
                    "concurrency": (info.get("pool") or {}).get("max-concurrency"),
                }
            )

        payload = {
            "workers_online": len(ping or {}),
            "workers": workers,
            "queues": {},
        }

        _WORKER_CACHE["payload"] = payload
        _WORKER_CACHE["ts"] = now
        return Response(payload)


class UsageReportView(APIView):
    permission_classes = [APIKeyRequired, HasScope]
    required_scopes = ["dashboard:read"]

    def get(self, request):
        api_key = request.auth
        jobs = IngestionJob.objects.filter(tenant=api_key.tenant, duration_ms__isnull=False)

        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")
        if date_from:
            jobs = jobs.filter(finished_at__gte=date_from)
        if date_to:
            jobs = jobs.filter(finished_at__lte=date_to)

        aggregates = jobs.aggregate(
            total_duration_ms=Sum("duration_ms"),
            avg_duration_ms=Avg("duration_ms"),
            job_count=Count("id"),
        )

        payload = {
            "from": date_from,
            "to": date_to,
            "job_count": aggregates["job_count"] or 0,
            "total_duration_ms": aggregates["total_duration_ms"] or 0,
            "avg_duration_ms": int(aggregates["avg_duration_ms"])
            if aggregates["avg_duration_ms"] is not None
            else None,
        }
        return Response(payload)

# Create your views here.
