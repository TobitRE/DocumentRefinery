# Endpoints

Auth: `Authorization: Api-Key <token>`

## Documents
- `POST /v1/documents/` — upload PDF (multipart `file`, optional `ingest`, `options_json`)
- `GET /v1/documents/` — list documents (tenant-scoped)
- `GET /v1/documents/{id}/` — document detail (tenant-scoped)

## Artifacts
- `GET /v1/artifacts/` — list artifacts (optional `job_id` filter)
- `GET /v1/artifacts/{id}/` — download artifact

## Jobs
- `GET /v1/jobs/` — list jobs (filters: `status`, `stage`, `document_id`, `created_after`, `created_before`)
- `GET /v1/jobs/{id}/` — job detail
- `POST /v1/jobs/{id}/cancel/` — cancel a queued/running job
- `POST /v1/jobs/{id}/retry/` — retry a failed/quarantined job

## Admin
- `GET /admin/` — Django admin

## Dashboard UI
- `GET /dashboard/` — staff-only dashboard page (uses API key for data)

## Health
- `GET /healthz` — basic liveness check
- `GET /readyz` — readiness (DB + broker)
- `GET /metrics` — basic Prometheus-style metrics

## Dashboard
- `GET /v1/dashboard/summary` — job counts, durations, failures
- `GET /v1/dashboard/workers` — Celery worker status
- `GET /v1/dashboard/reports/usage?from=...&to=...` — usage totals (duration + job count)
