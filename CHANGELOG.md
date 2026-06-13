# Changelog

All notable project-level changes are documented here.

## v0.1.0 - 2026-06-12

Beta baseline for DocumentRefinery.

### Added

- Tenant-scoped API key authentication with scopes, admin lifecycle actions,
  per-key Docling defaults, and per-key upload MIME allowlists.
- Document upload API for PDF, DOCX, PPTX, and XLSX inputs with MIME/signature
  validation, size limits, quarantine storage, SHA-256 dedupe, and optional
  immediate ingestion.
- Celery pipeline for ClamAV scan, Docling conversion, artifact export, optional
  chunk generation, final job status, timings, runtime metadata, and result
  metrics.
- Artifact records and downloads for Docling JSON, Markdown, text, DocTags,
  chunks JSON, and figure ZIPs, including preview endpoints and optional
  `X-Accel-Redirect` delivery.
- Job list/detail APIs with filters, cancel, retry, stage/status tracking,
  duration fields, attempt counters, and sanitized options output.
- Docling profile/capability/option resolution endpoints and PDF profile
  comparison jobs.
- Webhook endpoint management and `job.updated` deliveries with HMAC signatures,
  retry/backoff handling, delivery history, URL validation, and private-address
  protection.
- Staff dashboard pages for operations, uploads, jobs, profile comparisons,
  Docling profiles, API keys, webhooks, webhook deliveries, runtime diagnostics,
  and system status.
- Dashboard APIs for job summaries, worker inspection, usage reporting, and
  runtime diagnostics.
- Internal health, readiness, and metrics endpoints protected by
  `INTERNAL_ENDPOINTS_TOKEN`.
- Retention cleanup tasks for expired artifacts, expired documents, and infected
  quarantine files, scheduled through Celery Beat.
- Single-host deployment helpers for Gunicorn, Celery, Celery Beat, Redis,
  ClamAV, Nginx, TLS setup, model warmup, updates, and optional PostgreSQL.

### Known Limitations

- API key hashes are coupled to Django `SECRET_KEY`; rotating `SECRET_KEY` breaks
  lookup for existing keys unless keys are reissued or migrated.
- Webhook secrets are stored in plaintext in the database so workers can sign
  outbound deliveries.
- Tests for ClamAV, Docling conversion, Celery/Webhook delivery behavior, and
  external service boundaries use mocks/fakes rather than live services.
- Storage is local filesystem only; object storage and multi-host shared storage
  are not implemented.
- Tenant quotas, billing, and automated disk-pressure controls are not
  implemented.

### Documentation

- Rewrote `README.md` for the beta state with a real feature list, architecture
  diagram, API surface, quickstart, deployment notes, and known limitations.
- Archived the original implementation checklist at
  `docs/archive/docling_django_task_list.md`.
- Updated `AGENTS.md` with the fresh local coverage run: 240 tests passed,
  92% total coverage.
