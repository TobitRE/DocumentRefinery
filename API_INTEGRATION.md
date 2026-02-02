# DocumentRefinery API Integration Guide

Product: DocumentRefinery (NFX Systems GmbH)

DocumentRefinery ingests PDF documents, runs malware scanning and Docling-based
conversion, and produces structured outputs (JSON, text, markdown, chunks, and
figures) that downstream services can fetch via API.

This guide describes how to integrate another service (e.g. a Django app) with the
DocumentRefinery API for PDF ingestion and processing.

## Base URL

All API endpoints are under:

```
https://docex.nfx-systems.com/v1/
```

## Authentication

Use an API key in the `Authorization` header:

```
Authorization: Api-Key NFX_DOC_EX_API_KEY
```

The API key must include the scopes required by each endpoint.

### Scopes

- `documents:read` — list/retrieve documents
- `documents:write` — upload documents
- `artifacts:read` — list/download artifacts
- `jobs:read` — list/retrieve jobs
- `dashboard:read` — dashboard summary/workers endpoints

## Upload a PDF (async recommended)

Endpoint:

```
POST /v1/documents/
```

Multipart fields:
- `file` (required) — PDF file
- `ingest` (optional, boolean) — set `true` to start processing
- `options_json` (optional) — Docling options JSON

Constraints:
- Only PDF is accepted (`application/pdf` or `application/x-pdf`).
- Max size is controlled by `UPLOAD_MAX_SIZE_MB` (default 50 MB).

Example (Python):

```python
import json
import requests

BASE = "https://docex.nfx-systems.com"
API_KEY = "NFX_DOC_EX_API_KEY"

with open("/path/to/file.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE}/v1/documents/",
        headers={"Authorization": f"Api-Key {API_KEY}"},
        files={"file": f},
        data={
            "ingest": "true",
            "options_json": json.dumps({"languages": ["en"]}),
        },
        timeout=60,
    )
resp.raise_for_status()
payload = resp.json()
print(payload)
```

Response fields include:
- `id`, `uuid`, `original_filename`, `sha256`, `size_bytes`, `status`, `created_at`
- `job_id` when `ingest=true`

## Track progress

Ingestion is async. Use the job endpoints to track progress.

```
GET /v1/jobs/{id}/
```

Status values:
- `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELED`, `QUARANTINED`

Stage values:
- `SCANNING`, `CONVERTING`, `EXPORTING`, `CHUNKING`, `FINALIZING`

Useful fields:
- `stage`, `status`
- `scan_ms`, `convert_ms`, `export_ms`, `chunk_ms`
- `error_code`, `error_message`, `error_details_json`

Example poll:

```python
job = requests.get(
    f"{BASE}/v1/jobs/{job_id}/",
    headers={"Authorization": f"Api-Key {API_KEY}"},
    timeout=30,
).json()
```

## Retrieve artifacts

List artifacts for a job:

```
GET /v1/artifacts/?job_id=<job_id>
```

Download artifact:

```
GET /v1/artifacts/{id}/
```

Artifacts are served either directly or via `X-Accel-Redirect` (nginx) if enabled.

Artifact kinds (current):
- `docling_json`, `markdown`, `text`, `doctags`, `chunks_json`, `figures_zip`

## Jobs list and filtering

```
GET /v1/jobs/
```

Filters:
- `status`
- `stage`
- `document_id`
- `created_after` / `created_before` (ISO 8601)

Example:

```
GET /v1/jobs/?status=FAILED&created_after=2026-01-01T00:00:00
```

## Cancel / retry jobs

Cancel:

```
POST /v1/jobs/{id}/cancel/
```

Retry (only `FAILED`/`QUARANTINED`):

```
POST /v1/jobs/{id}/retry/
```

## Dashboard API (optional)

```
GET /v1/dashboard/summary
GET /v1/dashboard/workers
```

Requires scope: `dashboard:read`.

## Health endpoints (internal)

```
GET /healthz
GET /readyz
GET /metrics
```

If `INTERNAL_ENDPOINTS_TOKEN` is set, send it as:

```
X-Internal-Token: <token>
```

or via `?token=...` query parameter.

## Recommended integration flow (Django)

1) Upload PDF with `ingest=true`.
2) Store returned `document_id` and `job_id` in your app DB.
3) Poll `/v1/jobs/{id}/` until `SUCCEEDED` or `FAILED`.
4) Fetch artifacts from `/v1/artifacts/?job_id=...`.
5) Download and store desired artifact(s).

### Sync vs async

Processing is asynchronous (Celery). The API does not provide a synchronous
conversion endpoint. If you need a synchronous UX, block on polling (with a
timeout) and return progress in your UI.

## Common error codes

- `UNSUPPORTED_MEDIA_TYPE` — non-PDF upload
- `FILE_TOO_LARGE` — file exceeds size limit
- `DUPLICATE_DOCUMENT` — same document already uploaded for the tenant
- `INVALID_OPTIONS` — Docling options JSON invalid

## Troubleshooting

- `403` / `401`: check API key and scopes.
- `404` for artifacts: ingestion not finished or artifact not produced.
- Stuck jobs: check worker status via `/v1/dashboard/workers`.
