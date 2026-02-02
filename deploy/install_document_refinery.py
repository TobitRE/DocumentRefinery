#!/usr/bin/env python3
import os
import pwd
import secrets
import shutil
import subprocess
import sys
from pathlib import Path


GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"


def print_step(title: str) -> None:
    print(f"\n{CYAN}========================================{RESET}")
    print(f"{CYAN} STEP: {title} {RESET}")
    print(f"{CYAN}========================================{RESET}")


def ask_user(prompt: str, default: bool | None = None) -> bool:
    suffix = " [y/n]"
    if default is True:
        suffix = " [Y/n]"
    elif default is False:
        suffix = " [y/N]"
    while True:
        res = input(f"{YELLOW}{prompt}{suffix}: {RESET}").strip().lower()
        if not res and default is not None:
            return default
        if res in ("y", "yes"):
            return True
        if res in ("n", "no"):
            return False


def get_input(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    res = input(f"{YELLOW}{prompt}{suffix}: {RESET}").strip()
    return res or (default or "")


def run_cmd(command, shell: bool = False) -> bool:
    try:
        printable = command if isinstance(command, str) else " ".join(command)
        print(f"Executing: {GREEN}{printable}{RESET}")
        subprocess.check_call(command, shell=shell)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"{RED}Error: {exc}{RESET}")
        return False


def write_file(path: Path, content: str, mode: int | None = None) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        if mode is not None:
            os.chmod(path, mode)
        return True
    except Exception as exc:
        print(f"{RED}Write Error: {exc}{RESET}")
        return False


def require_root() -> None:
    if os.geteuid() != 0:
        print(f"{RED}Please run as root (sudo).{RESET}")
        sys.exit(1)


def find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "requirements.txt").exists() and (candidate / "document_refinery").exists():
            return candidate
    print(f"{RED}Could not locate repo root from {start}.{RESET}")
    sys.exit(1)


def user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def ensure_user(username: str) -> None:
    if user_exists(username):
        return
    print(f"{YELLOW}User '{username}' does not exist.{RESET}")
    if ask_user(f"Create system user '{username}'?", default=True):
        run_cmd(["useradd", "-r", "-m", "-s", "/bin/false", username])
    else:
        print(f"{RED}Cannot continue without a valid service user.{RESET}")
        sys.exit(1)


def render_env(template_text: str, overrides: dict[str, str]) -> str:
    lines = []
    seen = set()
    for line in template_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in overrides:
            lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def main() -> None:
    require_root()

    repo_root = find_repo_root(Path(__file__).resolve())
    repo_owner = pwd.getpwuid(repo_root.stat().st_uid).pw_name

    print(f"{GREEN}=== DocumentRefinery Install Script ==={RESET}")
    print(f"Repo: {repo_root}")

    print_step("Service User")
    service_user = get_input("Service user", repo_owner)
    ensure_user(service_user)
    if service_user != repo_owner:
        print(
            f"{YELLOW}Repo owner is '{repo_owner}'. Services will run as '{service_user}'.{RESET}"
        )
        if ask_user(f"Change ownership of repo to '{service_user}'?", default=False):
            run_cmd(["chown", "-R", f"{service_user}:{service_user}", str(repo_root)])

    print_step("Virtual Environment")
    default_venv = repo_root.parent / "venv"
    venv_dir = Path(get_input("Venv directory (one level above repo)", str(default_venv)))
    venv_dir = venv_dir.expanduser().resolve()
    venv_bin = venv_dir / "bin"
    venv_pip = venv_bin / "pip"
    venv_python = venv_bin / "python"

    print_step("System Dependencies")
    deps = ["python3-venv", "python3-pip", "nginx", "redis-server", "clamav-daemon"]
    if ask_user("Install OCR deps (tesseract-ocr, libgl1)?", default=True):
        deps.extend(["tesseract-ocr", "libgl1"])
    if ask_user("Install UFW firewall package?", default=False):
        deps.append("ufw")
    install_certbot = False
    if ask_user("Install certbot (for TLS)?", default=False):
        deps.extend(["certbot", "python3-certbot-nginx"])
        install_certbot = True
    if ask_user(f"Install packages: {' '.join(deps)}?", default=True):
        run_cmd(["apt", "update"])
        run_cmd(["apt", "install", "-y", *deps])

    run_cmd(["systemctl", "enable", "--now", "redis-server"])
    run_cmd(["systemctl", "enable", "--now", "clamav-daemon"])
    run_cmd(["systemctl", "enable", "--now", "nginx"])

    print_step("Python Environment")
    if not venv_dir.exists():
        if ask_user(f"Create venv at {venv_dir}?", default=True):
            run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
    if not venv_pip.exists():
        print(f"{RED}pip not found in {venv_pip}. Aborting.{RESET}")
        sys.exit(1)
    run_cmd([str(venv_pip), "install", "--upgrade", "pip"])
    run_cmd([str(venv_pip), "install", "-r", str(repo_root / "requirements.txt")])

    print_step("Docling Smoke Test")
    if ask_user("Run Docling conversion test (CPU/GPU detection)?", default=True):
        smoke_code = r"""
import shutil
import sys
import tempfile
from pathlib import Path

from docling.document_converter import DocumentConverter

pdf_bytes = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >> endobj
4 0 obj << /Length 44 >> stream
BT /F1 18 Tf 10 100 Td (Hello Docling) Tj ET
endstream endobj
xref
0 5
0000000000 65535 f
0000000010 00000 n
0000000060 00000 n
0000000117 00000 n
0000000200 00000 n
trailer << /Root 1 0 R /Size 5 >>
startxref
290
%%EOF
"""

with tempfile.TemporaryDirectory() as tmp:
    pdf_path = Path(tmp) / "test.pdf"
    pdf_path.write_bytes(pdf_bytes)
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    pages = len(doc.pages) if getattr(doc, "pages", None) is not None else 0
    print(f"Docling OK. Pages: {pages}")

gpu_tool = shutil.which("nvidia-smi")
print(f"GPU driver detected (nvidia-smi): {'yes' if gpu_tool else 'no'}")
try:
    import torch  # type: ignore
    cuda_ok = torch.cuda.is_available()
    device = torch.cuda.get_device_name(0) if cuda_ok else "n/a"
    print(f"PyTorch CUDA available: {cuda_ok} ({device})")
except Exception as exc:
    print(f"PyTorch CUDA check skipped: {exc}")
"""
        run_cmd([str(venv_python), "-c", smoke_code])

    print_step("Environment Configuration")
    env_example = repo_root / ".env.example"
    env_path = repo_root / ".env"
    if not env_example.exists():
        print(f"{RED}Missing {env_example}.{RESET}")
        sys.exit(1)
    if env_path.exists():
        if not ask_user(f"{env_path} exists. Overwrite?", default=False):
            print("Keeping existing .env.")
        else:
            env_path.unlink()

    debug_mode = ask_user("Enable DEBUG mode?", default=False)
    domain_name = ""
    if ask_user("Do you have a domain for Nginx?", default=False):
        domain_name = get_input("Domain name (example: docs.example.com)")
    allowed_hosts_default = domain_name or "localhost,127.0.0.1"
    allowed_hosts = get_input("ALLOWED_HOSTS (comma-separated)", allowed_hosts_default)
    data_root = get_input("DATA_ROOT", "/var/lib/docling_service")
    broker_url = get_input("CELERY_BROKER_URL", "redis://localhost:6379/0")

    internal_token = ""
    if ask_user("Protect /healthz,/readyz,/metrics with a token?", default=True):
        internal_token = secrets.token_urlsafe(32)
        print(f"{GREEN}Internal token: {internal_token}{RESET}")
        print(f"{YELLOW}Send as X-Internal-Token header or ?token=...{RESET}")

    secret_key = get_input("SECRET_KEY (leave blank to auto-generate)", "")
    if not secret_key:
        secret_key = secrets.token_urlsafe(48)

    if not env_path.exists():
        template_text = env_example.read_text(encoding="utf-8")
        overrides = {
            "SECRET_KEY": secret_key,
            "DEBUG": "true" if debug_mode else "false",
            "ALLOWED_HOSTS": allowed_hosts,
            "DATA_ROOT": data_root,
            "CELERY_BROKER_URL": broker_url,
            "INTERNAL_ENDPOINTS_TOKEN": internal_token,
        }
        write_file(env_path, render_env(template_text, overrides), mode=0o640)

    run_cmd(["chown", f"{service_user}:{service_user}", str(env_path)])

    print_step("Data Directory")
    Path(data_root).mkdir(parents=True, exist_ok=True)
    run_cmd(["chown", "-R", f"{service_user}:{service_user}", data_root])

    print_step("Database")
    run_cmd([str(venv_python), str(repo_root / "document_refinery" / "manage.py"), "migrate"])
    if ask_user("Create Django superuser now?", default=False):
        run_cmd([str(venv_python), str(repo_root / "document_refinery" / "manage.py"), "createsuperuser"])

    print_step("Systemd Services")
    socket_path = "/run/document_refinery/document_refinery.sock"
    gunicorn_service = f"""[Unit]
Description=DocumentRefinery Gunicorn
After=network.target

[Service]
User={service_user}
Group={service_user}
WorkingDirectory={repo_root}
EnvironmentFile={env_path}
RuntimeDirectory=document_refinery
RuntimeDirectoryMode=0755
ExecStart={venv_bin}/gunicorn \\
    --workers 3 \\
    --bind unix:{socket_path} \\
    config.wsgi:application
Restart=on-failure
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
"""

    celery_worker_service = f"""[Unit]
Description=DocumentRefinery Celery Worker
After=network.target

[Service]
User={service_user}
Group={service_user}
WorkingDirectory={repo_root}
EnvironmentFile={env_path}
ExecStart={venv_bin}/celery -A config worker --loglevel=INFO
Restart=on-failure
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
"""

    celery_beat_service = f"""[Unit]
Description=DocumentRefinery Celery Beat
After=network.target

[Service]
User={service_user}
Group={service_user}
WorkingDirectory={repo_root}
EnvironmentFile={env_path}
ExecStart={venv_bin}/celery -A config beat --loglevel=INFO
Restart=on-failure
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
"""

    units = {
        "gunicorn.service": gunicorn_service,
        "celery-worker.service": celery_worker_service,
        "celery-beat.service": celery_beat_service,
    }
    for name, content in units.items():
        unit_path = Path("/etc/systemd/system") / name
        if unit_path.exists():
            if not ask_user(f"{unit_path} exists. Overwrite?", default=False):
                continue
        write_file(unit_path, content)

    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "enable", "--now", "gunicorn.service"])
    run_cmd(["systemctl", "enable", "--now", "celery-worker.service"])
    if ask_user("Enable celery-beat.service?", default=False):
        run_cmd(["systemctl", "enable", "--now", "celery-beat.service"])

    print_step("Nginx")
    server_name = domain_name if domain_name else "_"
    nginx_conf = f"""server {{
    listen 80;
    server_name {server_name};

    client_max_body_size 60m;

    location / {{
        proxy_pass http://unix:{socket_path};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location /protected/ {{
        internal;
        alias {data_root}/;
    }}
}}
"""
    nginx_path = Path("/etc/nginx/sites-available/document_refinery")
    if nginx_path.exists():
        if ask_user(f"{nginx_path} exists. Overwrite?", default=False):
            write_file(nginx_path, nginx_conf)
    else:
        write_file(nginx_path, nginx_conf)
    enabled_path = Path("/etc/nginx/sites-enabled/document_refinery")
    if enabled_path.exists():
        enabled_path.unlink()
    enabled_path.symlink_to(nginx_path)
    default_site = Path("/etc/nginx/sites-enabled/default")
    if default_site.exists():
        if ask_user("Remove default nginx site?", default=True):
            default_site.unlink()
    run_cmd(["nginx", "-t"])
    run_cmd(["systemctl", "reload", "nginx"])

    print_step("Firewall")
    if shutil.which("ufw") and ask_user("Configure UFW?", default=False):
        run_cmd(["ufw", "allow", "OpenSSH"])
        run_cmd(["ufw", "allow", "22/tcp"])
        run_cmd(["ufw", "allow", "Nginx Full"])
        run_cmd("echo 'y' | ufw enable", shell=True)

    print_step("TLS")
    if domain_name and install_certbot:
        if ask_user("Request TLS certificate with certbot?", default=True):
            email = get_input("Certbot email")
            run_cmd(
                [
                    "certbot",
                    "--nginx",
                    "-d",
                    domain_name,
                    "--agree-tos",
                    "--email",
                    email,
                    "--no-eff-email",
                ]
            )

    print_step("Verification")
    if domain_name:
        print(f"{GREEN}Try: https://{domain_name}/healthz{RESET}")
    else:
        print(f"{GREEN}Try: http://<server-ip>/healthz{RESET}")
    print(f"{GREEN}Service status: systemctl status gunicorn.service{RESET}")

    print_step("Done")
    print(f"Repo: {repo_root}")
    print(f"Venv: {venv_dir}")
    print(f"Data root: {data_root}")


if __name__ == "__main__":
    main()
