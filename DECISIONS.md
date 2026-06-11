# Decisions

As of January 30, 2026.

## Product scope
- v0 includes the dashboard UI.
- Chunking is deferred to v1.

## Auth model
- API keys are per-tenant (no per-user keys for v0).
- Key defaults for Docling options are editable in Django Admin (API config later).

## Storage & delivery
- `DATA_ROOT` defaults to `/var/lib/docling_service`, configurable via `.env`.
- Artifact downloads use Nginx `X-Accel-Redirect`.

## Processing
- Celery broker is Redis.
- Max upload size is 50 MB.

## Retention
- No delete endpoint initially.
- New documents and artifacts get `expires_at` from environment defaults
  (`DOCUMENT_RETENTION_DAYS`, `ARTIFACT_RETENTION_DAYS`) with nullable tenant overrides.
- A value of `0` means unlimited retention and leaves `expires_at` empty.
- Celery Beat schedules `cleanup_expired_artifacts` and `cleanup_expired_documents` hourly.
- INFECTED document quarantine files have a separate
  `INFECTED_QUARANTINE_RETENTION_DAYS` window; cleanup removes the file and empty directories while
  preserving the document row for audit/history.

## Scale assumptions
- Unknown; track basic stats (job counts, sizes, durations) to inform tuning.

## Ideas for later
- CI pipeline (tests + lint + type checks).
- Encrypt webhook secrets at rest (field-level encryption / KMS).
- Dashboard CSP hardening: move inline JS to static file or add nonce-based CSP.
