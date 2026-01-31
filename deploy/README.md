# Deployment templates

These files are example templates for a single-host deployment on Ubuntu.

- `systemd/` — unit files for gunicorn, celery worker, and celery beat
- `nginx/` — Nginx site config with X-Accel-Redirect

Adjust paths, users, and environment variables for your server.
