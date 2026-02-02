#!/bin/bash

# Update script for DocumentRefinery (Ubuntu 24.04, nginx + systemd)
# Run from the repo root.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if [ ! -d ".git" ]; then
  print_error "Not in a git repository. Run from the repo root."
  exit 1
fi

if [ ! -f "document_refinery/manage.py" ]; then
  print_error "document_refinery/manage.py not found. Run from repo root."
  exit 1
fi

VENV_PATH="${VENV_PATH:-../venv}"
PY_BIN="${VENV_PATH}/bin/python"
PIP_BIN="${VENV_PATH}/bin/pip"

if [ ! -x "${PY_BIN}" ]; then
  print_error "Python not found at ${PY_BIN}. Set VENV_PATH or create the venv."
  exit 1
fi

print_status "Pulling latest changes from main..."
git fetch origin
git checkout main
git pull origin main

print_status "Installing/updating Python dependencies..."
${PIP_BIN} install -r requirements.txt

print_status "Running migrations..."
${PY_BIN} document_refinery/manage.py migrate

print_status "Restarting services..."
sudo systemctl restart gunicorn.service
sudo systemctl restart celery-worker.service

if sudo systemctl list-unit-files | grep -q '^celery-beat.service'; then
  sudo systemctl restart celery-beat.service || print_warning "celery-beat restart failed"
fi

print_status "Reloading nginx..."
sudo nginx -t && sudo systemctl reload nginx

print_status "Warming up..."
if [ -f ".env" ]; then
  INTERNAL_TOKEN=$(grep '^INTERNAL_ENDPOINTS_TOKEN=' .env | cut -d '=' -f2-)
fi
if [ -n "${INTERNAL_TOKEN:-}" ]; then
  curl -s -H "X-Internal-Token: ${INTERNAL_TOKEN}" http://localhost/healthz >/dev/null || \
    print_warning "Warm-up failed"
else
  curl -s http://localhost/healthz >/dev/null || print_warning "Warm-up failed"
fi

print_status "Update completed successfully."

