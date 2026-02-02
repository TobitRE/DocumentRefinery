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

## Quickstart

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/python document_refinery/manage.py migrate
./venv/bin/python document_refinery/manage.py createsuperuser
./venv/bin/python document_refinery/manage.py runserver
```

In another terminal, start a Celery worker:

```bash
./venv/bin/celery -A config worker --loglevel=INFO
```

Minimal upload example:

```bash
curl -X POST http://localhost:8000/v1/documents/ \
  -H "Authorization: Api-Key <your-key>" \
  -F "file=@sample.pdf"
```

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
- It configures `STATIC_ROOT` and runs `collectstatic` during install.
- It installs and enables `clamav-freshclam` for signature updates.
- It can run a Docling smoke test and will prompt for an email-based superuser.
- It can configure PostgreSQL and set `DATABASE_URL` when requested.
- The dashboard includes a staff-only system stats panel at `/dashboard/`.

Resume mode (skip destructive steps by default):

```bash
sudo python3 deploy/install_document_refinery.py --resume
```

Only overwrite nginx config (reads existing `.env` for `STATIC_ROOT`/`DATA_ROOT`):

```bash
sudo python3 deploy/install_document_refinery.py --only-nginx
```

Skip migrations during install (e.g. for read-only DB access):

```bash
sudo python3 deploy/install_document_refinery.py --skip-migrate
```

Help and non-interactive inputs:

```bash
python3 deploy/install_document_refinery.py -h
sudo python3 deploy/install_document_refinery.py --domain docs.example.com --certbot-email admin@example.com --request-tls
```

## Update script

For deployments using systemd + nginx, you can update in-place from the repo root:

```bash
./deploy/update_document_refinery.sh
```

This script pulls `main`, installs dependencies from `requirements.txt`, runs migrations,
restarts `gunicorn.service` and `celery-worker.service` (and `celery-beat.service` if present),
reloads nginx, and warms up `/healthz`.

Backups run by default (env + sqlite DB). Disable with `--no-backup`.

```bash
./deploy/update_document_refinery.sh --no-backup
```

You can also set a custom backup directory:

```bash
./deploy/update_document_refinery.sh --backup-dir /var/backups/document_refinery
```

To include `DATA_ROOT` artifacts (can be large):

```bash
./deploy/update_document_refinery.sh --backup --backup-data-root
```

Note: backing up `DATA_ROOT` requires read permissions (run with sudo if needed).

If `DATABASE_URL` points to PostgreSQL and `pg_dump` is available, the update script will
also save a database dump in the backup directory.

To update a different branch:

```bash
./deploy/update_document_refinery.sh --branch release
```

## Environment variables

Expected configuration values:
- `DJANGO_SETTINGS_MODULE`
- `SECRET_KEY`
- `DATA_ROOT`
- `STATIC_ROOT`
- `UPLOAD_MAX_SIZE_MB`
- `MAX_PAGES`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND` (optional)
- `ALLOWED_HOSTS`
- `DATABASE_URL` (optional, defaults to SQLite)
- `INTERNAL_ENDPOINTS_TOKEN` (optional)
- `CORS_ALLOWED_ORIGINS` (if needed)

## References

See `docling_django_task_list.md` for the detailed architecture and task plan.

## Operational docs

- `DEPLOYMENT.md`
- `EXTERNAL_SERVICES.md`
