# Endpoints

Auth: `Authorization: Api-Key <token>`

## Documents
- `POST /v1/documents/` — upload PDF (multipart `file`, optional `ingest`, `options_json`, `profile`, `external_uuid`)
- `POST /v1/documents/{id}/compare/` — create comparison jobs for a document (JSON body `profiles`, optional `options_json`)
- `GET /v1/documents/` — list documents (tenant-scoped)
- `GET /v1/documents/{id}/` — document detail (tenant-scoped)

## Artifacts
- `GET /v1/artifacts/` — list artifacts (optional `job_id` filter)
- `GET /v1/artifacts/{id}/` — download artifact

## Jobs
- `GET /v1/jobs/` — list jobs (filters: `status`, `stage`, `document_id`, `external_uuid`, `comparison_id`, `created_after`, `created_before`, `updated_after`)  
  - `created_after`/`created_before` must be ISO8601 (e.g. `2026-01-31T12:00:00`)
- `GET /v1/jobs/{id}/` — job detail
- `POST /v1/jobs/{id}/cancel/` — cancel a queued/running job (best-effort; attempts to revoke worker task)
- `POST /v1/jobs/{id}/retry/` — retry a failed/quarantined job

## Webhooks
- `POST /v1/webhooks/` — create webhook endpoint (`webhooks:write`)
- `GET /v1/webhooks/` — list webhook endpoints (`webhooks:read`)
- `GET /v1/webhooks/{id}/` — webhook detail (`webhooks:read`)
- `PATCH /v1/webhooks/{id}/` — update webhook endpoint (`webhooks:write`)
- `DELETE /v1/webhooks/{id}/` — delete webhook endpoint (`webhooks:write`)

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
