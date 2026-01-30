# Endpoints

Auth: `Authorization: Api-Key <token>`

## Documents
- `POST /v1/documents` — upload PDF (multipart `file`, optional `ingest`, `options_json`)
- `GET /v1/documents` — list documents (tenant-scoped)
- `GET /v1/documents/{id}` — document detail (tenant-scoped)

## Artifacts
- `GET /v1/artifacts` — list artifacts (optional `job_id` filter)
- `GET /v1/artifacts/{id}` — download artifact

## Admin
- `GET /admin/` — Django admin

## Dashboard
- `GET /v1/dashboard/summary` — job counts, durations, failures
- `GET /v1/dashboard/workers` — Celery worker status
