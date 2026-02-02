# DocumentRefinery

Docling-based document extraction service built on Django, DRF, and Celery.

Status: planning and task breakdown live in `docling_django_task_list.md`.

## Overview

This service is intended to:
- accept PDF uploads via REST API (API-key required)
- virus-scan uploads with ClamAV before processing
- run Docling conversion and export artifacts (JSON/Markdown/Text/DocTags/chunks)
- execute heavy work asynchronously via Celery
- serve artifacts from local filesystem, optionally via Nginx `X-Accel-Redirect`
- provide Django Admin for operations and a minimal dashboard

## Repo contents

- `docling_django_task_list.md` — implementation plan and checklist
- `README.md` — project overview and setup notes

## Quickstart (placeholder)

Implementation is not yet in this repository. Once the Django project is added,
this section should include:
- environment setup steps
- migrations and initial admin user creation
- how to run the API server and Celery worker
- a minimal `curl` upload example

## Install script (single host)

An interactive installer is available for Ubuntu-based single-host deployments.
It assumes the repo is already cloned and you run it from within the repo.

```bash
sudo python3 deploy/install_document_refinery.py
```

Notes:
- The virtualenv is created one level above the repo (default `../venv`).
- The script can generate `.env` from `.env.example`, install system deps,
  configure systemd + nginx, and optionally request TLS via certbot.
- The dashboard includes a staff-only system stats panel at `/dashboard/`.

## Environment variables (planned)

Expected configuration values (to be finalized in the implementation):
- `DJANGO_SETTINGS_MODULE`
- `SECRET_KEY`
- `DATA_ROOT`
- `UPLOAD_MAX_SIZE_MB`
- `MAX_PAGES`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND` (optional)
- `ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS` (if needed)

## References

See `docling_django_task_list.md` for the detailed architecture and task plan.

## Operational docs

- `DEPLOYMENT.md`
- `EXTERNAL_SERVICES.md`
