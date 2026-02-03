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
- `webhooks:read` — list/retrieve webhook endpoints
- `webhooks:write` — create/update/delete webhook endpoints

## Upload a PDF (async recommended)

Endpoint:

```
POST /v1/documents/
```

Multipart fields:
- `file` (required) — PDF file
- `ingest` (optional, boolean) — set `true` to start processing
- `options_json` (optional) — Docling options JSON
- `profile` (optional) — extraction profile name
- `external_uuid` (optional, UUID) — your correlation ID, echoed on documents and jobs

Constraints:
- Only PDF is accepted (`application/pdf` or `application/x-pdf`).
- Max size is controlled by `UPLOAD_MAX_SIZE_MB` (default 50 MB).

Docling options (current support):
- `max_num_pages` (int >= 0) — passed to the Docling converter; default from `MAX_PAGES` or unlimited.
- `max_file_size` (int >= 0, bytes) — passed to the converter; default from `UPLOAD_MAX_SIZE_MB`.
- `exports` (list of strings) — controls which artifacts are generated. Default: `["markdown", "text", "doctags"]`.
- `ocr` (bool) — validated but not yet wired to the converter (reserved).
- `ocr_languages` (list of strings) — validated but not yet wired (reserved).

### Extraction profiles

If you want to avoid sending Docling options on every request, use `profile`.
When a profile is provided, DocRefinery sets the pipeline options internally
and overrides `exports` with the profile defaults (other `options_json` keys
like `max_num_pages` are still honored).

Supported profiles:
- `fast_text` — born-digital PDFs, lowest latency.
- `ocr_only` — scanned PDFs, OCR-only.
- `structured` — OCR + table/layout focus.
- `full_vlm` — OCR + tables + image enrichment (highest fidelity).

Profile defaults (current):
- `fast_text`: `do_ocr=False`, `do_table_structure=False`, `do_picture_description=False`,
  `do_picture_classification=False`, exports `["text", "markdown", "doctags"]`.
- `ocr_only`: `do_ocr=True`, `ocr_options.lang=["auto"]`, `force_full_page_ocr=True`,
  no table/image enrichments, exports `["text", "markdown", "doctags"]`.
- `structured`: `do_ocr=True`, `do_table_structure=True`, `generate_parsed_pages=True`,
  exports `["text", "markdown", "doctags", "chunks_json"]`.
- `full_vlm`: `do_ocr=True`, `do_table_structure=True`, `do_picture_description=True`,
  `do_picture_classification=True`, `generate_picture_images=True`, `images_scale=2.0`,
  exports `["text", "markdown", "doctags", "chunks_json", "figures_zip"]`.

Unknown keys are accepted but currently ignored unless implemented in the pipeline.
If `options_json` is omitted, defaults come from the API key or tenant settings.

Docling official options (reference):
- `PdfPipelineOptions` exposes fields such as `do_ocr`, `do_table_structure`,
  `do_picture_description`, `do_picture_classification`, `document_timeout`,
  `force_backend_text`, and output helpers like `generate_page_images`,
  `generate_parsed_pages`, `generate_picture_images`, `generate_table_images`,
  plus `images_scale`, `layout_options`, `ocr_options`, and
  `table_structure_options`.
- OCR backends are configured via `ocr_options` and `OcrEngine` values such as
  `AUTO`, `EASYOCR`, `OCRMAC`, `RAPIDOCR`, `TESSERACT`, and `TESSERACT_CLI`.
- `OcrOptions` include `lang`, `force_full_page_ocr`, and `bitmap_area_threshold`.
  `TesseractOcrOptions` adds `path` and `psm` fields for Tesseract configuration.
- Docling examples show full-page OCR with `do_ocr=True` and `force_full_page_ocr=True`,
  and Tesseract language detection using `lang=["auto"]` with
  `TesseractCliOcrOptions`.

Official references:
- https://docling-project.github.io/docling/reference/pipeline_options/
- https://docling-project.github.io/docling/examples/full_page_ocr/
- https://docling-project.github.io/docling/examples/tesseract_lang_detection/

This API currently maps only the subset listed above. If you want additional
Docling options exposed, specify which keys you need and how they should map to
the pipeline (and whether unknown keys should be rejected or passed through).

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
            "options_json": json.dumps({
                "exports": ["markdown", "text", "doctags"],
                "max_num_pages": 50,
            }),
        },
        timeout=60,
    )
resp.raise_for_status()
payload = resp.json()
print(payload)
```

Response fields include:
- `id`, `uuid`, `external_uuid`, `original_filename`, `sha256`, `size_bytes`, `status`, `created_at`
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
- `profile`
- `scan_ms`, `convert_ms`, `export_ms`, `chunk_ms`
- `error_code`, `error_message`, `error_details_json`
- `external_uuid`

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

Notes:
- `chunks_json` contains Docling document tokens (DocTags) in JSON:
  `{"format": "doctags", "content": "<doctag>...</doctag>"}`.
- `figures_zip` is a zip of figure images generated by Docling when
  picture image export is enabled (may be empty if no figures are found).

## Jobs list and filtering

```
GET /v1/jobs/
```

Filters:
- `status`
- `stage`
- `document_id`
- `external_uuid`
- `created_after` / `created_before` (ISO 8601)
- `updated_after` (ISO 8601, uses `modified_at`)

Example:

```
GET /v1/jobs/?status=FAILED&created_after=2026-01-01T00:00:00
```

Example (changes since timestamp):

```
GET /v1/jobs/?updated_after=2026-02-01T12:00:00
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

`/healthz` returns a JSON payload including `docling_version` when available.

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

## Webhooks (optional)

Required scopes:
- `webhooks:write` to create/update/delete endpoints
- `webhooks:read` to list/retrieve endpoints

Create a webhook endpoint:

```
POST /v1/webhooks/
```

Fields:
- `name` (required)
- `url` (required)
- `secret` (optional) — used for HMAC signing
- `events` (optional, list of strings) — defaults to `["job.updated"]`
- `enabled` (optional, boolean) — defaults to `true`

Example registration (curl):

```bash
curl -X POST https://docex.nfx-systems.com/v1/webhooks/ \
  -H "Authorization: Api-Key NFX_DOC_EX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My App",
    "url": "https://example.com/webhooks/docrefinery",
    "secret": "supersecret",
    "events": ["job.updated"],
    "enabled": true
  }'
```

List endpoints:

```
GET /v1/webhooks/
```

Job update event:
- `event`: `job.updated`
- Fired when `status` or `stage` changes.

Example payload:

```json
{
  "event": "job.updated",
  "job_id": 123,
  "job_uuid": "9c1c3a6a-0a3a-4c45-9b6c-9a33f6c8b1a2",
  "document_id": 456,
  "external_uuid": "2f3b12aa-7c4b-4d2e-8a01-8b9d6b6d8d4f",
  "status": "RUNNING",
  "stage": "CONVERTING",
  "previous_status": "QUEUED",
  "previous_stage": "SCANNING",
  "error_code": "",
  "error_message": "",
  "error_details": null,
  "queued_at": "2026-02-03T12:00:00Z",
  "started_at": "2026-02-03T12:00:05Z",
  "finished_at": null,
  "created_at": "2026-02-03T12:00:00Z",
  "modified_at": "2026-02-03T12:00:05Z"
}
```

Signature headers (if `secret` is set):
- `X-DocRefinery-Signature: sha256=<hex>` — HMAC SHA-256 of the raw request body
- `X-DocRefinery-Event: job.updated`
- `X-DocRefinery-Delivery: <uuid>`

## Reducing polling load

Job tracking can rely on polling. For high volume or long-running jobs, this
creates unnecessary API traffic and delays status updates. The current approach
most integrators use:

1) Upload document and store the returned `job_id`.
2) Poll `GET /v1/jobs/{id}/` on a backoff schedule.
3) On completion, download artifacts and start downstream processing.

If you need lower polling overhead, use:

- Webhooks to receive `job.updated` on status or stage changes.
- `GET /v1/jobs/?updated_after=<timestamp>` to pull only changed jobs.
- `external_uuid` to reconcile jobs and documents with your internal IDs.
