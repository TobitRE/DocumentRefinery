# Deployment templates

These files are example templates for a single-host deployment on Ubuntu.

- `systemd/` — unit files for gunicorn, celery worker, and celery beat
- `nginx/` — Nginx site config with X-Accel-Redirect
- `docling_runtime_check.py` — server-side Docling dependency/runtime diagnostics
- `DOCLING_2_96_UPGRADE.md` — upgrade guide for Docling 2.96

Adjust paths, users, and environment variables for your server.
