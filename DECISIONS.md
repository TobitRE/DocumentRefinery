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
- Plan for `expires_at` and scheduled cleanup.

## Scale assumptions
- Unknown; track basic stats (job counts, sizes, durations) to inform tuning.
