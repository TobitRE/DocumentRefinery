# Docling Extraction Service — Django + DRF + Celery (Local FS) — Task List

This document is a **workable implementation task list** for a Django system that:
- Accepts PDF uploads via REST API (API-Key required)
- Stores files on **local filesystem**
- Runs **virus scan (ClamAV)** as a gate
- Uses **Docling** to convert PDFs into structured extraction artifacts (JSON, Markdown/Text/DocTags, Chunks)
- Executes heavy work via **Celery background tasks**
- Runs behind **Nginx + Gunicorn**
- Provides **Django Admin** for operations
- Provides a **small dashboard** (UI + API) with job/process counts & statuses **scoped per API key**
- Persists **duration & cost-relevant timings** per job (for later cost calculation)
- Uses a shared `BaseModel` from `core` app for all models (provided separately)

---

## 1) High-level architecture decisions (record once)

- [ ] **Local FS layout** (example, adjust to your server policy):
  - [ ] `DATA_ROOT=/var/lib/docling_service/`
  - [ ] `uploads/quarantine/<tenant_id>/<doc_id>.pdf`
  - [ ] `uploads/clean/<tenant_id>/<doc_id>.pdf`
  - [ ] `artifacts/<tenant_id>/<job_id>/docling.json`
  - [ ] `artifacts/<tenant_id>/<job_id>/document.md`
  - [ ] `artifacts/<tenant_id>/<job_id>/document.txt`
  - [ ] `artifacts/<tenant_id>/<job_id>/document.doctags`
  - [ ] `artifacts/<tenant_id>/<job_id>/chunks.json` (or NDJSON)
  - [ ] `logs/<date>/...` (optional)
- [ ] **Permissions model** for local FS:
  - [ ] Create dedicated system user/group (e.g., `docling-svc`)
  - [ ] Ensure Gunicorn + Celery run under same user/group or shared group with controlled umask
  - [ ] Nginx should not need direct write access
- [ ] **Broker/backend** for Celery:
  - [ ] Choose Redis or RabbitMQ (Redis is simpler for MVP)
- [ ] **Artifact delivery strategy**:
  - [ ] Option A (simple): Django streams artifact files
  - [ ] Option B (recommended): Nginx `X-Accel-Redirect` for efficient file downloads (Django authorizes; Nginx serves)

---

## 2) Project setup & deployment scaffolding (Ubuntu 24.04, Nginx, Gunicorn, Celery)

### 2.1 Django project & settings
- [ ] Create Django project + apps:
  - [ ] `core` (contains `BaseModel`, utilities)
  - [ ] `accounts` or `authn` (API keys, tenants)
  - [ ] `documents` (Document, Job, Artifact, Chunk exports)
  - [ ] `dashboard` (UI views + API endpoints)
- [ ] Settings:
  - [ ] `.env` support (e.g., `django-environ`)
  - [ ] Separate `settings/dev.py`, `settings/prod.py`
  - [ ] `DATA_ROOT`, `UPLOAD_MAX_SIZE_MB`, `MAX_PAGES`, `DOC_DEFAULT_OPTIONS`
  - [ ] Logging configuration (JSON logs recommended)
  - [ ] CORS policy (if dashboard UI is separate)
- [ ] Pin dependencies (Docling version pinned; reproducibility).

### 2.2 Celery integration
- [ ] Add Celery app (e.g., `config/celery.py`) and configure:
  - [ ] `CELERY_BROKER_URL`
  - [ ] `CELERY_RESULT_BACKEND` (or disable results; store status in DB)
  - [ ] `task_acks_late=True` for robustness
  - [ ] Task timeouts:
    - [ ] `CELERY_TASK_TIME_LIMIT` (hard kill)
    - [ ] `CELERY_TASK_SOFT_TIME_LIMIT`
  - [ ] Worker concurrency tuned for CPU/RAM
- [ ] Provide a **single ingestion pipeline** orchestrated by Celery:
  - [ ] `scan_pdf_task`
  - [ ] `docling_convert_task`
  - [ ] `export_artifacts_task`
  - [ ] `chunking_task` (optional but recommended)
  - [ ] `finalize_job_task`
- [ ] Add Celery Beat only if scheduled tasks needed (cleanup/retention).

### 2.3 Process manager (systemd)
- [ ] Systemd units:
  - [ ] `gunicorn.service`
  - [ ] `celery-worker.service`
  - [ ] `celery-beat.service` (optional)
  - [ ] `redis.service` (if local Redis)
  - [ ] `clamav-daemon.service` (`clamd`)
- [ ] Ensure consistent environment variables for all services.

### 2.4 Nginx reverse proxy
- [ ] Nginx site config:
  - [ ] Proxy to Gunicorn socket/port
  - [ ] Request size limits matching upload constraints
  - [ ] Timeouts (uploads may be large; avoid proxy timeouts)
- [ ] If using `X-Accel-Redirect`:
  - [ ] Nginx internal location block to serve artifacts securely.

**Acceptance criteria**
- [ ] `curl` upload works end-to-end in staging
- [ ] Celery worker consumes jobs and updates DB
- [ ] Nginx → Gunicorn → Django works behind TLS (if applicable)

---

## 3) Security & authentication (API key required)

### 3.1 API key model & authentication
- [ ] Implement API key auth for DRF:
  - [ ] Header: `Authorization: Api-Key <token>`
  - [ ] Store only **hashed** tokens
  - [ ] Key metadata: `name`, `tenant`, `scopes`, `active`, `created_at`, `last_used_at`
- [ ] Add scopes/permissions:
  - [ ] `documents:write`, `documents:read`, `jobs:read`, `artifacts:read`, `dashboard:read`
- [ ] Add rate limiting per key (DRF throttling):
  - [ ] burst + sustained limits

### 3.2 Data isolation per API key / tenant
- [ ] Every Document/Job/Artifact record must include:
  - [ ] `tenant_id`
  - [ ] `created_by_key_id` (or equivalent) to enforce key-level scoping
- [ ] DRF queryset filtering by `request.auth` (API key owner):
  - [ ] Key can only see its own tenant’s objects (or strictly its own key’s objects, depending on your policy)

**Acceptance criteria**
- [ ] All endpoints reject missing/invalid key
- [ ] Cross-tenant access is impossible via API

---

## 4) Django models (using `core.BaseModel`)

> All models below must inherit from the `BaseModel` shipped in `core` (you will provide it).  
> Avoid duplicating timestamps that `BaseModel` already provides; add only what’s needed.

### 4.1 Models to implement (minimum)

#### `Document` (inherits `BaseModel`)
- [ ] Fields:
  - [ ] `tenant` (FK)
  - [ ] `created_by_key` (FK to API key)
  - [ ] `original_filename`
  - [ ] `sha256` (unique per tenant, optional)
  - [ ] `mime_type`
  - [ ] `size_bytes`
  - [ ] `storage_relpath_quarantine`
  - [ ] `storage_relpath_clean` (nullable until scan ok)
  - [ ] `status` (UPLOADED, CLEAN, INFECTED, DELETED)
  - [ ] Optional: `page_count` (if extracted later)
- [ ] Methods:
  - [ ] `get_quarantine_path()`, `get_clean_path()`

#### `IngestionJob` (inherits `BaseModel`)
- [ ] Fields:
  - [ ] `tenant`, `created_by_key` (FK)
  - [ ] `document` (FK)
  - [ ] `status` (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELED, QUARANTINED)
  - [ ] `stage` (SCANNING, CONVERTING, EXPORTING, CHUNKING, FINALIZING)
  - [ ] `options_json` (docling + chunking + export options)
  - [ ] `docling_version` (string)
  - [ ] Timing fields (for cost):
    - [ ] `queued_at`
    - [ ] `started_at`
    - [ ] `finished_at`
    - [ ] `duration_ms` (computed)
    - [ ] Optional: per-stage timings `scan_ms`, `convert_ms`, `export_ms`, `chunk_ms`
  - [ ] `attempt` / `max_retries`
  - [ ] `error_code`, `error_message`, `error_details_json`
  - [ ] Worker info (optional but helpful):
    - [ ] `worker_hostname`, `celery_task_id`
- [ ] Methods:
  - [ ] `mark_started()`, `mark_finished()`, `recompute_durations()`

#### `Artifact` (inherits `BaseModel`)
- [ ] Fields:
  - [ ] `tenant`, `created_by_key` (FK)
  - [ ] `job` (FK)
  - [ ] `kind` (docling_json, markdown, text, doctags, chunks_json, figures_zip, etc.)
  - [ ] `storage_relpath`
  - [ ] `checksum_sha256`
  - [ ] `size_bytes`
  - [ ] Optional: `content_type`
- [ ] Index:
  - [ ] `(tenant, job, kind)` unique

#### Optional: `JobEvent` / `JobLog` (inherits `BaseModel`)
- [ ] Fields:
  - [ ] `job`
  - [ ] `level` (INFO/WARN/ERROR)
  - [ ] `message`
  - [ ] `payload_json`
  - [ ] `timestamp`

**Acceptance criteria**
- [ ] All objects are tenant-scoped
- [ ] Timings are persisted and queryable for cost reporting

---

## 5) File handling on local filesystem

### 5.1 Upload handling
- [ ] Stream upload to disk (avoid loading entire PDF into memory)
- [ ] Enforce constraints:
  - [ ] max file size
  - [ ] allowed MIME types
- [ ] Compute `sha256` while streaming
- [ ] Write to `quarantine` path
- [ ] Create `Document` record and initial `IngestionJob` if requested

### 5.2 Artifact writing
- [ ] All artifacts are written under `artifacts/<tenant>/<job>/...`
- [ ] Write atomically:
  - [ ] write to temp file → fsync → rename
- [ ] Store checksums + sizes in DB
- [ ] Optional: gzip large JSON outputs

### 5.3 Retention / cleanup tasks
- [ ] Scheduled cleanup (Celery Beat optional):
  - [ ] delete old quarantine files
  - [ ] purge artifacts older than retention policy
  - [ ] remove orphan files

---

## 6) Virus scanning (ClamAV)

- [ ] Install and configure `clamd` (daemon mode recommended for speed)
- [ ] Celery task: `scan_pdf_task(document_id, job_id)`
  - [ ] Scan quarantine file
  - [ ] If infected:
    - [ ] mark Document status INFECTED
    - [ ] mark Job QUARANTINED / FAILED with `error_code=VIRUS_FOUND`
    - [ ] do not proceed to conversion
  - [ ] If clean:
    - [ ] move/copy to clean path
    - [ ] update Document status CLEAN

**Acceptance criteria**
- [ ] No Docling conversion runs before `SCAN_OK`

---

## 7) Docling integration (conversion + exports + chunking)

### 7.1 Conversion task
- [ ] Celery task: `docling_convert_task(job_id)`
  - [ ] Read options from `options_json`
  - [ ] Convert from clean PDF path to a DoclingDocument
  - [ ] Persist docling version used (`docling.__version__`)

### 7.2 Export artifacts task
- [ ] Celery task: `export_artifacts_task(job_id)`
  - [ ] Always export **lossless docling JSON**
  - [ ] Optional exports based on `options_json`:
    - [ ] Markdown
    - [ ] Strict text
    - [ ] DocTags
  - [ ] Create `Artifact` records

### 7.3 Chunking task (service output, no embeddings)
- [ ] Celery task: `chunking_task(job_id)`
  - [ ] Use Docling chunkers on DoclingDocument (recommended)
  - [ ] Output `chunks.json` structure:
    - [ ] `chunk_id`
    - [ ] `text`
    - [ ] `context_text` (if enabled)
    - [ ] `metadata` (page(s), headings, element types)
  - [ ] Store as Artifact

### 7.4 Robustness constraints
- [ ] Enforce docling-side limits (from options):
  - [ ] `max_num_pages`
  - [ ] `max_file_size`
- [ ] Apply Celery time limits to prevent stuck PDFs
- [ ] Failure handling:
  - [ ] store stack trace summary in `error_details_json` (avoid leaking secrets)

**Acceptance criteria**
- [ ] Artifacts are generated deterministically for a known test PDF set
- [ ] Job is resumable/retryable without corrupting outputs

---

## 8) REST API endpoints (tenant/key-scoped)

### 8.1 Documents
- [ ] `POST /v1/documents` — upload PDF
- [ ] `GET /v1/documents` — list (scoped)
- [ ] `GET /v1/documents/{id}` — detail
- [ ] `POST /v1/documents/{id}/ingest` — create ingestion job (with options)

### 8.2 Jobs (process monitoring)
- [ ] `GET /v1/jobs` — list jobs for this API key/tenant
  - [ ] filters: `status`, `stage`, `document_id`, `created_after`, `created_before`
- [ ] `GET /v1/jobs/{id}` — job detail (status, stage, timings, errors, artifacts)
- [ ] `POST /v1/jobs/{id}/cancel` — cancel if still queued/running (best-effort)
- [ ] `POST /v1/jobs/{id}/retry` — retry failed jobs (respect max retries)

### 8.3 Artifacts
- [ ] `GET /v1/artifacts?job_id=...`
- [ ] `GET /v1/artifacts/{id}/download`
  - [ ] Enforce key scope
  - [ ] Use streaming or X-Accel-Redirect

### 8.4 Dashboard API (per API key)
- [ ] `GET /v1/dashboard/summary`
  - [ ] counts: queued/running/succeeded/failed (scoped to key’s tenant or key)
  - [ ] running stages breakdown
  - [ ] avg/median job duration (last N days)
  - [ ] total processing time (sum duration) for cost reporting
- [ ] `GET /v1/dashboard/workers`
  - [ ] worker count (Celery inspect)
  - [ ] active tasks count per worker
  - [ ] queue length (if broker supports, or approximation)
- [ ] `GET /v1/dashboard/jobs/active`
  - [ ] active/running job list with started_at + elapsed time

**Acceptance criteria**
- [ ] All dashboard endpoints return **only** data allowed for the calling API key

---

## 9) Small dashboard UI (web)

### 9.1 Minimal UI requirements
- [ ] A simple HTML page served by Django (no heavy SPA needed), showing:
  - [ ] Worker count and active tasks
  - [ ] Job counts by status for this tenant/key
  - [ ] Recently failed jobs with error_code
  - [ ] Average duration + total duration (cost basis)
- [ ] Access control:
  - [ ] Either:
    - [ ] staff-only internal dashboard (Django session auth), **and**
    - [ ] API provides per-key data programmatically
  - [ ] Or:
    - [ ] dashboard is also key-authenticated (e.g., via API token in header) — usually less convenient in browser

### 9.2 Worker/process monitoring approach
- [ ] Implement a lightweight “worker status collector”:
  - [ ] Use Celery `inspect` (`active`, `stats`, `ping`)
  - [ ] Cache results (e.g., 3–10 seconds) to avoid hammering broker
- [ ] Display “process count” as:
  - [ ] number of Celery workers online
  - [ ] active tasks per worker
  - [ ] (optional) configured concurrency per worker

**Acceptance criteria**
- [ ] Operators can spot stuck/running jobs and worker availability quickly

---

## 10) Django Admin (operations)

- [ ] Admin registrations:
  - [ ] Document admin:
    - [ ] list_display: tenant, filename, status, size, created_at
    - [ ] search_fields: filename, sha256
    - [ ] filters: tenant, status
  - [ ] IngestionJob admin:
    - [ ] list_display: tenant, document, status, stage, duration_ms, started_at, finished_at
    - [ ] filters: status, stage, tenant
    - [ ] readonly_fields: timings, error details
    - [ ] action: retry selected (optional, guarded)
  - [ ] Artifact admin:
    - [ ] list_display: kind, job, size, created_at
    - [ ] link to download (optional)
  - [ ] API key admin:
    - [ ] create/revoke/rotate keys (show only last 4 chars; never show full secret)

**Acceptance criteria**
- [ ] Admin can inspect failures and download artifacts for debugging

---

## 11) Testing plan (must-have)

### 11.1 Unit tests
- [ ] API-key auth:
  - [ ] missing/invalid token rejected
  - [ ] scope enforcement
- [ ] Tenant scoping:
  - [ ] key A cannot access key B documents/jobs/artifacts
- [ ] File handling:
  - [ ] streaming upload writes correct size + sha256
  - [ ] path traversal protection
- [ ] Job timing:
  - [ ] started/finished timestamps set correctly
  - [ ] duration_ms computed and stored
- [ ] Artifact logic:
  - [ ] kind uniqueness per job
  - [ ] checksum stored

### 11.2 Integration tests
- [ ] Virus scan pipeline:
  - [ ] mock clamd “clean” and “infected” results
- [ ] Docling conversion:
  - [ ] test PDF fixtures:
    - [ ] text-based
    - [ ] scanned (OCR on)
    - [ ] table-heavy
  - [ ] assert produced artifacts exist and have expected fields
- [ ] Celery orchestration:
  - [ ] run tasks in eager mode in tests OR use a test worker
- [ ] Dashboard endpoints:
  - [ ] correct counts and scoping

### 11.3 Load / reliability tests (lightweight)
- [ ] Upload 50–200 PDFs and verify:
  - [ ] no leaked files
  - [ ] no job stuck beyond time limit
  - [ ] DB indices adequate (job list queries fast)

**Acceptance criteria**
- [ ] CI runs unit + integration tests reliably
- [ ] Deterministic outputs for fixture PDFs (within pinned Docling version)

---

## 12) Observability & cost reporting foundations

- [ ] Persist durations:
  - [ ] wall-clock duration per job (required)
  - [ ] optional per-stage durations
- [ ] Store cost-relevant metrics:
  - [ ] page count (if available)
  - [ ] bytes processed
  - [ ] OCR enabled flag
  - [ ] docling options snapshot
- [ ] Logs:
  - [ ] correlate `request_id` → `job_id` → `celery_task_id`
- [ ] Expose a cost-report endpoint (later):
  - [ ] `GET /v1/reports/usage?from=...&to=...`
  - [ ] sum(duration_ms), avg(duration_ms), job counts per option type

---

## 13) Suggested implementation order (safe & fast)

1. [ ] Models + migrations (tenant/key-scoped + timings)
2. [ ] API-key authentication + scoping middleware/helpers
3. [ ] Upload endpoint → quarantine file write + Document record
4. [ ] Celery pipeline: scan → convert → export → (chunk) → finalize
5. [ ] Artifact download endpoint (streaming first, then optional X-Accel-Redirect)
6. [ ] Admin panels for Document/Job/Artifact/Key
7. [ ] Dashboard API (summary + worker stats)
8. [ ] Minimal dashboard UI consuming the dashboard API
9. [ ] Test suite completion + fixtures
10. [ ] Hardening: timeouts, retries, retention, logging/metrics

---

## Appendix A — Dashboard metrics (recommended fields)

For `/v1/dashboard/summary` (scoped per API key policy):
- `jobs`: `{queued, running, succeeded, failed, canceled}`
- `stages_running`: `{scanning, converting, exporting, chunking}`
- `durations_ms`: `{avg_24h, p50_24h, p95_24h, total_24h, total_30d}`
- `recent_failures`: list of `{job_id, document_id, error_code, finished_at}`
- `throughput`: `{jobs_24h, jobs_7d}`

For `/v1/dashboard/workers`:
- `workers_online`
- `workers`: list of `{hostname, active_tasks, pool, concurrency}` (best-effort)
- `queues`: `{default: depth}` (best-effort; broker-dependent)

---

## Appendix B — Options contract (store in `options_json`)

Store exactly what influences compute cost:
- OCR on/off + languages
- table structure on/off
- max pages / max size
- chunking strategy + settings
- export list
- docling pipeline settings versioned

This makes later cost modeling straightforward.
