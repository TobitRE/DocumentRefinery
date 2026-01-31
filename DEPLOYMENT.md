# Deployment Guide (Single Host)

This guide assumes Ubuntu 24.04, local filesystem storage, Redis, ClamAV, Nginx, and systemd.

## 1) Create service user and directories

```bash
sudo useradd -r -s /bin/false docling-svc
sudo mkdir -p /var/www/document_refinery
sudo mkdir -p /var/lib/docling_service
sudo chown -R docling-svc:docling-svc /var/www/document_refinery /var/lib/docling_service
```

## 2) Install system packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx redis-server clamav-daemon
sudo systemctl enable --now redis-server clamav-daemon
```

## 3) Deploy application code

```bash
cd /var/www/document_refinery
git clone <your-repo> .
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 4) Configure environment

Create `/var/www/document_refinery/.env` using `.env.example` as a template.

```bash
cp .env.example .env
```

Important values:
- `SECRET_KEY` (unique)
- `DEBUG=false`
- `ALLOWED_HOSTS=your.domain`
- `DATA_ROOT=/var/lib/docling_service`
- `CELERY_BROKER_URL=redis://localhost:6379/0`
- `CLAMAV_HOST=127.0.0.1`
- `X_ACCEL_REDIRECT_LOCATION=/protected`

## 5) Initialize database

```bash
./venv/bin/python document_refinery/manage.py migrate
./venv/bin/python document_refinery/manage.py createsuperuser
```

## 6) Systemd services

Copy unit files from `deploy/systemd/` and adjust paths if needed.

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn.service celery-worker.service
```

Optional:
```bash
sudo systemctl enable --now celery-beat.service
```

## 7) Nginx configuration

```bash
sudo cp deploy/nginx/document_refinery.conf /etc/nginx/sites-available/document_refinery
sudo ln -s /etc/nginx/sites-available/document_refinery /etc/nginx/sites-enabled/document_refinery
sudo nginx -t
sudo systemctl restart nginx
```

Ensure `X_ACCEL_REDIRECT_LOCATION` matches the Nginx `location /protected/` alias to `DATA_ROOT`.

## 8) Verify services

```bash
curl http://localhost/healthz
curl http://localhost/readyz
```

## 9) TLS (recommended)

Use Certbot or your preferred TLS termination to enable HTTPS.

## 10) Upgrades

```bash
cd /var/www/document_refinery
git pull
./venv/bin/pip install -r requirements.txt
./venv/bin/python document_refinery/manage.py migrate
sudo systemctl restart gunicorn.service celery-worker.service
```
