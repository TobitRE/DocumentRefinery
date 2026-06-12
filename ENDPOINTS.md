# Endpoints

Auth: `Authorization: Api-Key <token>`

API key upload policy:
- Upload endpoints enforce per-key `allowed_upload_mime_types`.
- Default allowed upload MIME types are `application/pdf` and `application/x-pdf`.

Pagination:
- Breaking change: v1 list endpoints for documents, jobs, artifacts, and webhooks
  return a page envelope instead of a bare array:
  `count`, `next`, `previous`, `results`.
- Default `page_size` is 50. Clients may pass `page_size`; values above 200 are capped.

Errors:
- API error responses use `error_code`, `message`, and `request_id`.
- The same request ID is also returned in the `X-Request-ID` response header.

Schema:
- `GET /v1/schema/` — OpenAPI 3 schema, protected by staff session or valid API key.

## Documents
- `POST /v1/documents/` — upload document (multipart `file`, optional `ingest`, `options_json`, `profile`, `external_uuid`, `duplicate_policy`)
  - request `Content-Type` must be allowed by the API key allowlist
  - for PDF content types, payload must also look like a PDF (`%PDF-` header)
  - duplicates are detected by tenant + SHA-256
  - default duplicate behavior is `409 DUPLICATE_DOCUMENT` with `duplicate`, `document_uuid`, `sha256`, and latest job summary fields
  - `duplicate_policy=return_existing` returns `200 OK` with the existing `document` and `latest_job`
- `POST /v1/documents/{document_uuid}/ingest/` — start/reuse processing for an existing tenant-owned document (`documents:write`)
  - JSON body: optional `mode`, `profile`, `options_json`
  - `mode=reuse_existing` returns an active/succeeded matching job or creates one if none exists
  - `mode=retry_failed` retries a matching failed/quarantined job if below retry limit
  - `mode=create_new` always creates a new job without overwriting old artifacts
  - document UUID lookup is tenant-scoped; foreign and missing UUIDs both return `404`
- `POST /v1/documents/{id}/compare/` — create comparison jobs for a document (JSON body `profiles`, optional `options_json`)
- `GET /v1/documents/` — paginated list of documents (tenant-scoped)
- `GET /v1/documents/{id}/` — document detail (tenant-scoped)

## Artifacts
- `GET /v1/artifacts/` — paginated list of artifacts (optional `job_id` filter)
- `GET /v1/artifacts/{id}/` — download artifact

## Jobs
- `GET /v1/jobs/` — paginated list of jobs (filters: `status`, `stage`, `document_id`, `external_uuid`, `comparison_id`, `created_after`, `created_before`, `updated_after`)  
  - `created_after`/`created_before` must be ISO8601 (e.g. `2026-01-31T12:00:00`)
- `GET /v1/jobs/{id}/` — job detail, including stable public `uuid`
- `POST /v1/jobs/{id}/cancel/` — cancel a queued/running job (`jobs:write`; best-effort revoke)
- `POST /v1/jobs/{id}/retry/` — retry a failed/quarantined job (`jobs:write`)

## Webhooks
- `POST /v1/webhooks/` — create webhook endpoint (`webhooks:write`)
- `GET /v1/webhooks/` — paginated list of webhook endpoints (`webhooks:read`)
- `GET /v1/webhooks/{id}/` — webhook detail (`webhooks:read`)
- `PATCH /v1/webhooks/{id}/` — update webhook endpoint (`webhooks:write`)
- `DELETE /v1/webhooks/{id}/` — delete webhook endpoint (`webhooks:write`)

## Breaking Changes
- List responses for `/v1/documents/`, `/v1/jobs/`, `/v1/artifacts/`, and
  `/v1/webhooks/` changed from a JSON array to a paginated object. Read rows from
  `results`.
- Validation/auth/not-found errors now use the common
  `{error_code, message, request_id}` shape. Existing endpoint-specific error
  fields, such as duplicate document metadata, may still be present.

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
