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
has_unit() {
  local unit="$1"
  local state
  state=$(systemctl show -p LoadState --value "${unit}" 2>/dev/null || true)
  [ "${state}" = "loaded" ]
}

DO_BACKUP=1
DO_BACKUP_DATA_ROOT=0
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BRANCH="${BRANCH:-main}"

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
    --branch)
      BRANCH="${2:-}"
      if [ -z "${BRANCH}" ]; then
        print_error "--branch requires a value"
        exit 1
      fi
      shift 2
      ;;
    --branch=*)
      BRANCH="${1#*=}"
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
git checkout "${BRANCH}"
git pull origin "${BRANCH}"

print_status "Installing/updating Python dependencies..."
${PIP_BIN} install -r requirements.txt

if [ "${DO_BACKUP}" -eq 1 ]; then
  print_status "Creating backup..."
  DATA_ROOT=""
  if [ -f ".env" ]; then
    DATA_ROOT=$(grep '^DATA_ROOT=' .env | cut -d '=' -f2-)
    DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d '=' -f2-)
    HF_HOME=$(grep '^HF_HOME=' .env | cut -d '=' -f2-)
  fi
  if [ -n "${DATABASE_URL:-}" ]; then
    DATABASE_URL="${DATABASE_URL%\"}"
    DATABASE_URL="${DATABASE_URL#\"}"
    DATABASE_URL="${DATABASE_URL%\'}"
    DATABASE_URL="${DATABASE_URL#\'}"
  fi
  if [ -z "${DATA_ROOT}" ]; then
    DATA_ROOT="/var/lib/docling_service"
  fi
  if [ -z "${HF_HOME:-}" ]; then
    HF_HOME=""
  fi
  DATA_ROOT="${DATA_ROOT%\"}"
  DATA_ROOT="${DATA_ROOT#\"}"
  DATA_ROOT="${DATA_ROOT%\'}"
  DATA_ROOT="${DATA_ROOT#\'}"
  HF_HOME="${HF_HOME%\"}"
  HF_HOME="${HF_HOME#\"}"
  HF_HOME="${HF_HOME%\'}"
  HF_HOME="${HF_HOME#\'}"
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
  if [ -n "${DATABASE_URL:-}" ] && echo "${DATABASE_URL}" | grep -q '^postgresql://'; then
    print_status "Detected PostgreSQL DATABASE_URL, attempting pg_dump..."
    if command -v pg_dump >/dev/null 2>&1; then
      ENV_DUMP="${BACKUP_DIR}/db_${TIMESTAMP}.dump"
      if pg_dump "${DATABASE_URL}" > "${ENV_DUMP}"; then
        print_status "Postgres dump written to ${ENV_DUMP}"
      else
        print_warning "pg_dump failed; skipping Postgres backup"
      fi
    else
      print_warning "pg_dump not available; install postgresql-client to enable Postgres backups"
    fi
  fi
  if [ "${DO_BACKUP_DATA_ROOT}" -eq 1 ]; then
    if [ -d "${DATA_ROOT}" ]; then
      print_status "Backing up DATA_ROOT (${DATA_ROOT})..."
      if [ "$(id -u)" -eq 0 ] || [ -r "${DATA_ROOT}" ]; then
        tar -czf "${BACKUP_DIR}/data_root_${TIMESTAMP}.tar.gz" -C "${DATA_ROOT}" .
      else
        print_warning "DATA_ROOT not readable; run with sudo to include it"
      fi
    else
      print_warning "DATA_ROOT not found; skipping data root backup"
    fi
  fi
  print_status "Backup written to ${BACKUP_DIR}"
fi

print_status "Running migrations..."
${PY_BIN} document_refinery/manage.py migrate

print_status "Collecting static files..."
${PY_BIN} document_refinery/manage.py collectstatic --noinput

print_status "Restarting services..."
if has_unit "gunicorn.service"; then
  sudo systemctl restart gunicorn
else
  print_warning "gunicorn service not found"
fi

if has_unit "celery-worker.service"; then
  sudo systemctl restart celery-worker
else
  print_warning "celery-worker service not found"
fi

if has_unit "celery-beat.service"; then
  sudo systemctl restart celery-beat || print_warning "celery-beat restart failed"
fi

print_status "Reloading nginx..."
sudo nginx -t && sudo systemctl reload nginx

print_status "Ensuring ClamAV is running..."
if has_unit "clamav-daemon.service"; then
  sudo systemctl enable --now clamav-daemon || print_warning "clamav-daemon start failed"
else
  print_warning "clamav-daemon service not found"
fi
if has_unit "clamav-freshclam.service"; then
  sudo systemctl enable --now clamav-freshclam || print_warning "clamav-freshclam start failed"
else
  print_warning "clamav-freshclam service not found"
fi

print_status "Checking ClamAV access..."
CLAMAV_SOCKET=""
DATA_ROOT=""
if [ -f ".env" ]; then
  CLAMAV_SOCKET=$(grep '^CLAMAV_SOCKET=' .env | cut -d '=' -f2-)
  DATA_ROOT=$(grep '^DATA_ROOT=' .env | cut -d '=' -f2-)
fi
if [ -z "${DATA_ROOT}" ]; then
  DATA_ROOT="/var/lib/docling_service"
fi
DATA_ROOT="${DATA_ROOT%\"}"
DATA_ROOT="${DATA_ROOT#\"}"
DATA_ROOT="${DATA_ROOT%\'}"
DATA_ROOT="${DATA_ROOT#\'}"

SERVICE_USER=""
if has_unit "celery-worker.service"; then
  SERVICE_USER=$(systemctl show -p User --value celery-worker.service 2>/dev/null || true)
fi
if [ -z "${SERVICE_USER}" ]; then
  SERVICE_USER="$(whoami)"
fi

HF_HOME_DEFAULT="${DATA_ROOT}/hf_cache"
if [ -z "${HF_HOME}" ]; then
  print_warning "HF_HOME not set; docling downloads may fail when ProtectHome=read-only. Add HF_HOME=${HF_HOME_DEFAULT} to .env"
else
  sudo mkdir -p "${HF_HOME}" || print_warning "Failed to create HF_HOME at ${HF_HOME}"
  sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${HF_HOME}" || print_warning "Failed to set ownership on ${HF_HOME}"
fi

if [ -n "${CLAMAV_SOCKET}" ]; then
  if [ -S "${CLAMAV_SOCKET}" ]; then
    SOCK_GROUP=$(stat -c %G "${CLAMAV_SOCKET}" 2>/dev/null || true)
    if [ -n "${SOCK_GROUP}" ] && ! id -nG "${SERVICE_USER}" | grep -qw "${SOCK_GROUP}"; then
      print_warning "${SERVICE_USER} is not in ${SOCK_GROUP}; adding to allow socket access"
      sudo usermod -aG "${SOCK_GROUP}" "${SERVICE_USER}" || print_warning "Failed to add ${SERVICE_USER} to ${SOCK_GROUP}"
    fi
  else
    print_warning "CLAMAV_SOCKET is set but socket not found: ${CLAMAV_SOCKET}"
  fi
else
  if [ -S "/run/clamav/clamd.ctl" ]; then
    print_warning "ClamAV is socket-activated. Consider setting CLAMAV_SOCKET=/run/clamav/clamd.ctl"
  fi
fi

if command -v setfacl >/dev/null 2>&1; then
  sudo find "${DATA_ROOT}" -type d -exec setfacl -m u:clamav:rx -m d:u:clamav:rx {} + || \
    print_warning "Failed to set clamd ACLs on DATA_ROOT directories"
  sudo find "${DATA_ROOT}" -type f -exec setfacl -m u:clamav:r {} + || \
    print_warning "Failed to set clamd ACLs on DATA_ROOT files"
else
  print_warning "setfacl not installed; cannot set clamd ACLs on DATA_ROOT"
fi

print_status "Warming up..."
if [ -f ".env" ]; then
  INTERNAL_TOKEN=$(grep '^INTERNAL_ENDPOINTS_TOKEN=' .env | cut -d '=' -f2-)
fi
if [ -n "${INTERNAL_TOKEN:-}" ]; then
  curl -s -H "X-Internal-Token: ${INTERNAL_TOKEN}" http://localhost/healthz >/dev/null || \
    print_warning "Warm-up failed"
else
  print_warning "INTERNAL_ENDPOINTS_TOKEN not set; skipping warm-up"
fi

print_status "Update completed successfully."
