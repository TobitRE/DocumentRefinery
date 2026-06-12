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

Use concrete endpoint paths under `/v1/` (for example `/v1/documents/`, `/v1/jobs/`).
Do not use `/v1/` itself as an integration health/auth check.

The OpenAPI 3 schema is available at:

```
GET /v1/schema/
```

`/v1/schema/` is protected. Use either a valid staff session or any valid API key.

## Authentication

Use an API key in the `Authorization` header:

```
Authorization: Api-Key NFX_DOC_EX_API_KEY
```

The API key must include the scopes required by each endpoint.

Upload policy per API key:
- Each API key has `allowed_upload_mime_types` (configured in the staff dashboard).
- Default is `application/pdf, application/x-pdf`.
- Uploads to `POST /v1/documents/` are rejected with `415 UNSUPPORTED_MEDIA_TYPE`
  when the request `Content-Type` is not in this allowlist.

### Scopes

- `documents:read` — list/retrieve documents
- `documents:write` — upload documents and start ingestion for owned documents
- `artifacts:read` — list/download artifacts
- `jobs:read` — list/retrieve jobs
- `jobs:write` — cancel/retry jobs
- `dashboard:read` — dashboard summary/workers endpoints
- `webhooks:read` — list/retrieve webhook endpoints
- `webhooks:write` — create/update/delete webhook endpoints

## Pagination

Breaking change: list endpoints now return a page object instead of a bare JSON
array. This applies to:

- `GET /v1/documents/`
- `GET /v1/jobs/`
- `GET /v1/artifacts/`
- `GET /v1/webhooks/`

Shape:

```json
{
  "count": 123,
  "next": "https://docex.nfx-systems.com/v1/jobs/?page=2",
  "previous": null,
  "results": []
}
```

Default page size is 50. Clients may pass `page_size`, capped at 200:

```
GET /v1/jobs/?status=FAILED&page_size=100
```

Read records from `results`, not from the top-level response.

## Error format

API errors use a consistent shape:

```json
{
  "error_code": "INVALID_OPTIONS",
  "message": "max_num_pages must be an integer.",
  "request_id": "b2e4e89d-0c39-4af9-a2e1-0e6f96ad4a9c"
}
```

The same ID is returned in the `X-Request-ID` response header. You may send
`X-Request-ID` on the request to provide your own correlation ID.

Some endpoint-specific errors include extra fields. For example,
`DUPLICATE_DOCUMENT` still includes duplicate document metadata in addition to
`error_code`, `message`, and `request_id`.

## Breaking changes

- List responses for documents, jobs, artifacts, and webhooks are now paginated
  objects. Existing clients that iterate the top-level JSON array must switch to
  `payload["results"]`.
- Error responses now consistently include `request_id`. Existing error-code
  checks should continue to use `payload["error_code"]`.

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
- `duplicate_policy` (optional) — `conflict` (default) or `return_existing`

Constraints:
- Request `Content-Type` must be allowed by the API key allowlist.
- Default allowed types are `application/pdf` and `application/x-pdf`.
- If type is PDF, content is also signature-checked (`%PDF-` header).
- Max size is controlled by `UPLOAD_MAX_SIZE_MB` (default 50 MB).

Docling options (current support):
- `max_num_pages` (int >= 0) — passed to the Docling converter; default from `MAX_PAGES` or unlimited.
- `max_file_size` (int >= 0, bytes) — passed to the converter; default from `UPLOAD_MAX_SIZE_MB`.
- `exports` (list of strings) — controls which artifacts are generated. Default: `["markdown", "text", "doctags"]`.
- `chunks_format` (`"hybrid"` or `"doctags_compat"`) — controls the `chunks_json` artifact format. Default: `"hybrid"`.
- `chunking` (object) — HybridChunker options for `chunks_json`; supported keys include `tokenizer`, `max_tokens`, `repeat_table_header`, `merge_peers`, `omit_header_on_overflow`, and `always_emit_headings`. Per-request Hugging Face tokenizers must be enabled by the deployment `DOCLING_ALLOWED_CHUNK_TOKENIZERS` allowlist and are loaded with `local_files_only=true`; the default tokenizer is `simple`.
- `ocr` (bool) — validated but not yet wired to the converter (reserved).
- `ocr_languages` (list of strings) — validated but not yet wired (reserved).

### Extraction profiles

If you want to avoid sending Docling options on every request, use `profile`.
When a profile is provided, DocRefinery sets the pipeline options internally
and overrides `exports` with the profile defaults. Profile chunking defaults
are used as a base, while request `chunking` keys like `max_tokens` still
override them.

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
            "duplicate_policy": "return_existing",
            "options_json": json.dumps({
                "exports": ["markdown", "text", "doctags"],
                "max_num_pages": 50,
            }),
        },
        timeout=60,
    )

if resp.status_code == 409:
    payload = resp.json()
    if payload.get("error_code") == "DUPLICATE_DOCUMENT":
        print("Document already exists:", payload["document_uuid"])
    else:
        resp.raise_for_status()
else:
    resp.raise_for_status()
payload = resp.json()
print(payload)
```

Response fields include:
- `id`, `uuid`, `external_uuid`, `original_filename`, `sha256`, `size_bytes`, `status`, `created_at`
- `job_id` when `ingest=true`

### Duplicate uploads

Documents are deduplicated per tenant by SHA-256. By default, uploading the same
PDF again returns `409 DUPLICATE_DOCUMENT` for backward compatibility. The error
payload includes the existing document and latest tenant-local job summary:

```json
{
  "error_code": "DUPLICATE_DOCUMENT",
  "message": "Document already exists.",
  "request_id": "b2e4e89d-0c39-4af9-a2e1-0e6f96ad4a9c",
  "duplicate": true,
  "document_id": 123,
  "document_uuid": "7c86f0fd-9de2-41ad-b0df-5ef5d221a35d",
  "sha256": "9e0f...",
  "latest_job_id": 456,
  "latest_job_uuid": "d677e8f8-6e96-4c88-a126-61ff0d753910",
  "latest_job_status": "SUCCEEDED"
}
```

The response also includes a `Location` header pointing at the existing document.
Integrations that use `raise_for_status()` should either handle this 409 before
raising, or send `duplicate_policy=return_existing`.

With `duplicate_policy=return_existing`, duplicate uploads return `200 OK`:

```json
{
  "duplicate": true,
  "document": {
    "id": 123,
    "uuid": "7c86f0fd-9de2-41ad-b0df-5ef5d221a35d",
    "external_uuid": null,
    "original_filename": "contract.pdf",
    "sha256": "9e0f...",
    "mime_type": "application/pdf",
    "size_bytes": 1048576,
    "status": "CLEAN",
    "page_count": 12,
    "created_at": "2026-05-22T08:55:00Z"
  },
  "latest_job": {
    "id": 456,
    "uuid": "d677e8f8-6e96-4c88-a126-61ff0d753910",
    "document_id": 123,
    "status": "SUCCEEDED",
    "stage": "FINALIZING",
    "profile": "fast_text"
  }
}
```

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
- `comparison_id`
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

This endpoint is paginated. Artifact rows are in `results`.

Download artifact:

```
GET /v1/artifacts/{id}/
```

Artifacts are served either directly or via `X-Accel-Redirect` (nginx) if enabled.

Artifact kinds (current):
- `docling_json`, `markdown`, `text`, `doctags`, `chunks_json`, `figures_zip`

Notes:
- `chunks_json` contains a JSON array of HybridChunker chunks by default. Each
  item includes `text` and `meta` with pages, headings, bounding boxes, and
  Docling item references when Docling provides them.
- To receive the legacy DocTags compatibility object, set
  `chunks_format: "doctags_compat"` in `options_json`; that emits
  `{"format": "doctags", "content": "<doctag>...</doctag>"}`.
- `figures_zip` is a zip of figure images generated by Docling when
  picture image export is enabled (may be empty if no figures are found).

## Jobs list and filtering

```
GET /v1/jobs/
```

This endpoint is paginated. Job rows are in `results`.

Filters:
- `status`
- `stage`
- `document_id`
- `external_uuid`
- `comparison_id`
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

## Start or reuse ingestion for an existing document

To process an already uploaded document again, use the document UUID endpoint.
The lookup is always scoped to the authenticated tenant; another tenant's UUID
and a non-existent UUID both return the same `404 Not Found`.

```
POST /v1/documents/{document_uuid}/ingest/
```

Requires scope: `documents:write`. `mode=retry_failed` also requires
`jobs:write`, because it mutates and requeues an existing job.

Body (JSON):
- `mode` (optional) — `reuse_existing` (default), `retry_failed`, or `create_new`
- `profile` (optional) — extraction profile name
- `options_json` (optional) — Docling options JSON

Mode semantics:
- `reuse_existing` returns a matching `QUEUED`, `RUNNING`, or `SUCCEEDED` job
  without starting a duplicate job. If no matching job exists, a new job is
  created.
- `retry_failed` retries the latest matching `FAILED` or `QUARANTINED` job when
  it is still below its retry limit. This mode requires `jobs:write`.
- `create_new` always creates a new job. Successful jobs and their artifacts are
  not overwritten.

The API copies the current clean or quarantine source file into a job-specific
source path before queuing a new/retried job. This keeps reprocessing from
depending on a quarantine file that may already have been moved during scanning.

Example:

```json
{
  "mode": "create_new",
  "profile": "fast_text",
  "options_json": {"max_num_pages": 50}
}
```

Response:

```json
{
  "mode": "create_new",
  "created": true,
  "reused": false,
  "retried": false,
  "document": {
    "id": 123,
    "uuid": "7c86f0fd-9de2-41ad-b0df-5ef5d221a35d",
    "sha256": "9e0f...",
    "status": "CLEAN"
  },
  "job": {
    "id": 789,
    "uuid": "21fbf45c-a02d-4a54-a45f-f5ddf8ac90c0",
    "document_id": 123,
    "profile": "fast_text",
    "status": "QUEUED",
    "stage": "SCANNING"
  },
  "job_id": 789,
  "job_uuid": "21fbf45c-a02d-4a54-a45f-f5ddf8ac90c0"
}
```

## Compare quality profiles

To run the same document with multiple profiles and compare the results,
call the compare endpoint. This creates multiple jobs that share a
`comparison_id` you can use to fetch or reconcile results.

```
POST /v1/documents/{id}/compare/
```

Body (JSON):
- `profiles` (required, list of strings) — e.g. `["fast_text", "structured"]`
- `options_json` (optional) — base Docling options applied to each profile

Response:
- `comparison_id`
- `document_id`
- `jobs` (list of `{job_id, profile}`)

Example:

```json
{
  "profiles": ["fast_text", "structured"],
  "options_json": {"max_num_pages": 50}
}
```

Use `comparison_id` to fetch all jobs:

```
GET /v1/jobs/?comparison_id=<id>
```

Read the matching jobs from the paginated response's `results` field.

## Cancel / retry jobs

Cancel:

```
POST /v1/jobs/{id}/cancel/
```

Retry (only `FAILED`/`QUARANTINED`):

```
POST /v1/jobs/{id}/retry/
```

Requires scope: `jobs:write`.

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

Send `INTERNAL_ENDPOINTS_TOKEN` as:

```
X-Internal-Token: <token>
```

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

- `UNSUPPORTED_MEDIA_TYPE` — upload content type is not allowed by API key (or invalid PDF payload)
- `FILE_TOO_LARGE` — file exceeds size limit
- `DUPLICATE_DOCUMENT` — same document already uploaded for the tenant
- `INVALID_OPTIONS` — Docling options JSON invalid
- `MISSING_SOURCE_FILE` — existing document has no readable clean/quarantine source file
- `NOT_RETRYABLE` — requested retry mode but no retryable job exists
- `RETRY_LIMIT` — retryable job has reached its retry limit

## Troubleshooting

- `401 Unauthorized`: API key authentication failed (missing/invalid header or key).
- `403 Forbidden`: API key is valid, but required scope is missing for that endpoint.
- `415`: check the API key `allowed_upload_mime_types` and request `Content-Type`.
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

This endpoint is paginated. Webhook endpoint rows are in `results`.

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
- `GET /v1/jobs/?updated_after=<timestamp>` to pull only changed jobs
  from the paginated `results` field.
- `external_uuid` to reconcile jobs and documents with your internal IDs.
