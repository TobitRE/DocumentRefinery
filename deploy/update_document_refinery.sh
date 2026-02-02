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

DO_BACKUP=1
DO_BACKUP_DATA_ROOT=0
BACKUP_DIR="${BACKUP_DIR:-./backups}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup)
      DO_BACKUP=1
      shift
      ;;
    --no-backup)
      DO_BACKUP=0
      shift
      ;;
    --backup-data-root)
      DO_BACKUP=1
      DO_BACKUP_DATA_ROOT=1
      shift
      ;;
    --backup-dir)
      DO_BACKUP=1
      BACKUP_DIR="${2:-}"
      if [ -z "${BACKUP_DIR}" ]; then
        print_error "--backup-dir requires a value"
        exit 1
      fi
      shift 2
      ;;
    --backup-dir=*)
      DO_BACKUP=1
      BACKUP_DIR="${1#*=}"
      shift
      ;;
    *)
      print_warning "Unknown argument: $1"
      shift
      ;;
  esac
done

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

if [ "${DO_BACKUP}" -eq 1 ]; then
  print_status "Creating backup..."
  DATA_ROOT=""
  if [ -f ".env" ]; then
    DATA_ROOT=$(grep '^DATA_ROOT=' .env | cut -d '=' -f2-)
  fi
  if [ -z "${DATA_ROOT}" ]; then
    DATA_ROOT="/var/lib/docling_service"
  fi
  TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
  mkdir -p "${BACKUP_DIR}"
  if [ -f ".env" ]; then
    cp ".env" "${BACKUP_DIR}/env_${TIMESTAMP}"
  else
    print_warning ".env not found; skipping env backup"
  fi
  if [ -f "document_refinery/db.sqlite3" ]; then
    cp "document_refinery/db.sqlite3" "${BACKUP_DIR}/db_${TIMESTAMP}.sqlite3"
  else
    print_warning "SQLite DB not found; skipping DB backup"
  fi
  if [ "${DO_BACKUP_DATA_ROOT}" -eq 1 ]; then
    if [ -d "${DATA_ROOT}" ]; then
      print_status "Backing up DATA_ROOT (${DATA_ROOT})..."
      sudo tar -czf "${BACKUP_DIR}/data_root_${TIMESTAMP}.tar.gz" -C "${DATA_ROOT}" .
    else
      print_warning "DATA_ROOT not found; skipping data root backup"
    fi
  fi
fi

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
