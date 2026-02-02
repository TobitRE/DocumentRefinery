#!/usr/bin/env python3
import grp
import os
import pwd
import secrets
import shutil
import subprocess
import sys
import urllib.parse
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


def run_cmd(command, shell: bool = False, required: bool = False) -> bool:
    try:
        printable = command if isinstance(command, str) else " ".join(command)
        print(f"Executing: {GREEN}{printable}{RESET}")
        subprocess.check_call(command, shell=shell)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"{RED}Error: {exc}{RESET}")
        if required:
            print(f"{RED}Aborting due to failed command.{RESET}")
            sys.exit(1)
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


def group_exists(groupname: str) -> bool:
    try:
        grp.getgrnam(groupname)
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


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in line:
                    continue
                key, value = stripped.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        return {}
    return values


def sanitize_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


def main() -> None:
    require_root()
    resume = "--resume" in sys.argv
    only_nginx = "--only-nginx" in sys.argv
    skip_migrate = "--skip-migrate" in sys.argv or only_nginx

    repo_root = find_repo_root(Path(__file__).resolve())
    project_dir = repo_root / "document_refinery"
    repo_owner = pwd.getpwuid(repo_root.stat().st_uid).pw_name
    socket_path = "/run/document_refinery/document_refinery.sock"
    install_fail2ban = False
    install_certbot = False
    install_postgres = False
    venv_dir = None
    venv_bin = None
    venv_pip = None
    venv_python = None

    print(f"{GREEN}=== DocumentRefinery Install Script ==={RESET}")
    print(f"Repo: {repo_root}")
    if resume:
        print(f"{YELLOW}Resume mode enabled: skipping destructive steps by default.{RESET}")
    if only_nginx:
        print(f"{YELLOW}Only-nginx mode enabled: other steps will be skipped.{RESET}")

    if not only_nginx:
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

    if not only_nginx:
        print_step("System Dependencies")
        deps = [
            "python3-venv",
            "python3-pip",
            "nginx",
            "redis-server",
            "clamav-daemon",
            "clamav-freshclam",
        ]
        install_packages = ask_user(
            "Install/ensure system packages?", default=not resume and not only_nginx
        )
        if install_packages:
            if ask_user("Install OCR deps (tesseract-ocr, libgl1)?", default=not resume):
                deps.extend(["tesseract-ocr", "libgl1"])
            if ask_user("Install UFW firewall package?", default=False):
                deps.append("ufw")
            if ask_user("Install PostgreSQL server?", default=False):
                deps.extend(["postgresql", "postgresql-contrib"])
                install_postgres = True
            if ask_user("Install fail2ban?", default=False):
                deps.append("fail2ban")
                install_fail2ban = True
            if ask_user("Install certbot (for TLS)?", default=False):
                deps.extend(["certbot", "python3-certbot-nginx"])
                install_certbot = True
            if ask_user(f"Install packages: {' '.join(deps)}?", default=True):
                run_cmd(["apt", "update"], required=True)
                run_cmd(["apt", "install", "-y", *deps], required=True)

    if not only_nginx:
        run_cmd(["systemctl", "enable", "--now", "redis-server"], required=True)
        run_cmd(["systemctl", "enable", "--now", "clamav-daemon"], required=True)
        run_cmd(["systemctl", "enable", "--now", "clamav-freshclam"], required=True)
        if ask_user("Run freshclam update now?", default=False):
            run_cmd(["systemctl", "stop", "clamav-freshclam"])
            run_cmd(["freshclam"])
            run_cmd(["systemctl", "start", "clamav-freshclam"], required=True)
        if install_fail2ban:
            run_cmd(["systemctl", "enable", "--now", "fail2ban"], required=True)
        if install_postgres:
            run_cmd(["systemctl", "enable", "--now", "postgresql"], required=True)
        run_cmd(["systemctl", "enable", "--now", "nginx"], required=True)

    if not only_nginx:
        print_step("Python Environment")
        if not venv_dir.exists():
            if ask_user(f"Create venv at {venv_dir}?", default=True):
                run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
        if not venv_pip.exists():
            print(f"{RED}pip not found in {venv_pip}. Aborting.{RESET}")
            sys.exit(1)
        if ask_user("Install/update Python dependencies?", default=not resume):
            run_cmd([str(venv_pip), "install", "--upgrade", "pip"], required=True)
            run_cmd(
                [str(venv_pip), "install", "-r", str(repo_root / "requirements.txt")],
                required=True,
            )

    if not only_nginx:
        print_step("Docling Smoke Test")
        if ask_user("Run Docling conversion test (CPU/GPU detection)?", default=not resume):
            smoke_code = r'''
import shutil
import sys
import tempfile
from pathlib import Path

from docling.document_converter import DocumentConverter

def build_pdf() -> bytes:
    header = b"%PDF-1.4\n"
    stream = b"BT /F1 18 Tf 10 100 Td (Hello Docling) Tj ET\n"
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n"
        ),
        b"4 0 obj << /Length %d >> stream\n" % len(stream)
        + stream
        + b"endstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    offsets = []
    current = len(header)
    for obj in objects:
        offsets.append(current)
        current += len(obj)
    xref_offset = current
    xref_lines = [b"xref\n", b"0 6\n", b"0000000000 65535 f \n"]
    for off in offsets:
        xref_lines.append(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = (
        b"trailer << /Root 1 0 R /Size 6 >>\n"
        b"startxref\n"
        + f"{xref_offset}\n".encode("ascii")
        + b"%%EOF\n"
    )
    return header + b"".join(objects) + b"".join(xref_lines) + trailer


pdf_bytes = build_pdf()

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
'''
            run_cmd([str(venv_python), "-c", smoke_code])

    print_step("Environment Configuration")
    env_example = repo_root / ".env.example"
    env_path = repo_root / ".env"
    if not env_example.exists():
        print(f"{RED}Missing {env_example}.{RESET}")
        sys.exit(1)
    env_values = read_env(env_path) if env_path.exists() else {}
    data_root = None
    static_root = None

    domain_name = ""
    if not only_nginx and ask_user("Do you have a domain for Nginx?", default=False):
        domain_name = get_input("Domain name (example: docs.example.com)")

    if env_path.exists() and resume:
        print(f"{YELLOW}Using existing .env in resume mode.{RESET}")
    elif only_nginx:
        if not env_path.exists():
            print(f"{RED}Missing .env; cannot continue in only-nginx mode.{RESET}")
            sys.exit(1)
    else:
        if env_path.exists():
            if not ask_user(f"{env_path} exists. Overwrite?", default=False):
                print("Keeping existing .env.")
            else:
                env_path.unlink()

        debug_mode = ask_user("Enable DEBUG mode?", default=False)
        allowed_hosts_default = domain_name or "localhost,127.0.0.1"
        allowed_hosts = get_input("ALLOWED_HOSTS (comma-separated)", allowed_hosts_default)
        data_root = get_input("DATA_ROOT", "/var/lib/docling_service")
        static_root = get_input("STATIC_ROOT", f"{repo_root.parent}/staticfiles")
        broker_url = get_input("CELERY_BROKER_URL", "redis://localhost:6379/0")

        database_url = None
        if ask_user("Use PostgreSQL for DATABASE_URL?", default=False):
            db_name = get_input("Postgres database name", "document_refinery")
            db_user = get_input("Postgres user", "docrefinery")
            db_password = get_input("Postgres password (leave blank to generate)", "")
            if not db_password:
                db_password = secrets.token_urlsafe(24)
                print(f"{GREEN}Generated Postgres password: {db_password}{RESET}")
            db_host = get_input("Postgres host", "localhost")
            db_port = get_input("Postgres port", "5432")

            if db_host in ("localhost", "127.0.0.1") and shutil.which("psql"):
                escaped_pw = db_password.replace("'", "''")
                role_check = (
                    f"SELECT 1 FROM pg_roles WHERE rolname='{db_user}';"
                )
                db_check = (
                    f"SELECT 1 FROM pg_database WHERE datname='{db_name}';"
                )
                try:
                    role_exists = subprocess.check_output(
                        ["sudo", "-u", "postgres", "psql", "-tAc", role_check],
                        text=True,
                    ).strip()
                    if role_exists != "1":
                        run_cmd(
                            [
                                "sudo",
                                "-u",
                                "postgres",
                                "psql",
                                "-c",
                                f"CREATE ROLE \"{db_user}\" WITH LOGIN PASSWORD '{escaped_pw}';",
                            ],
                            required=True,
                        )
                    db_exists = subprocess.check_output(
                        ["sudo", "-u", "postgres", "psql", "-tAc", db_check],
                        text=True,
                    ).strip()
                    if db_exists != "1":
                        run_cmd(
                            [
                                "sudo",
                                "-u",
                                "postgres",
                                "psql",
                                "-c",
                                f"CREATE DATABASE \"{db_name}\" OWNER \"{db_user}\";",
                            ],
                            required=True,
                        )
                except subprocess.CalledProcessError as exc:
                    print(f"{RED}Postgres setup failed: {exc}{RESET}")
            else:
                print(
                    f"{YELLOW}Skipping automatic Postgres setup (host={db_host}, psql not available).{RESET}"
                )

            user_enc = urllib.parse.quote_plus(db_user)
            pass_enc = urllib.parse.quote_plus(db_password)
            database_url = (
                f"postgresql://{user_enc}:{pass_enc}@{db_host}:{db_port}/{db_name}"
            )

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
                "STATIC_ROOT": static_root,
                "CELERY_BROKER_URL": broker_url,
                "INTERNAL_ENDPOINTS_TOKEN": internal_token,
            }
            if database_url:
                overrides["DATABASE_URL"] = database_url
            write_file(env_path, render_env(template_text, overrides), mode=0o640)

    if not data_root:
        env_data_root = sanitize_env_value(env_values.get("DATA_ROOT"))
        if env_data_root and not env_data_root.startswith("$"):
            data_root = env_data_root
        else:
            data_root = "/var/lib/docling_service"
    if not static_root:
        env_static_root = sanitize_env_value(env_values.get("STATIC_ROOT"))
        if env_static_root and not env_static_root.startswith("$"):
            static_root = env_static_root
        else:
            static_root = str(repo_root.parent / "staticfiles")

    if only_nginx:
        allowed_hosts_value = sanitize_env_value(env_values.get("ALLOWED_HOSTS")) or "_"
        default_host = allowed_hosts_value.split(",")[0].strip() or "_"
        if not domain_name:
            domain_name = get_input("Domain name for Nginx", default_host)
    else:
        run_cmd(["chown", f"{service_user}:{service_user}", str(env_path)])

        print_step("Data Directory")
        nginx_group_default = "www-data" if group_exists("www-data") else service_user
        nginx_group = get_input("Nginx file/socket group", nginx_group_default)
        if not group_exists(nginx_group):
            print(f"{RED}Group '{nginx_group}' does not exist. Using '{service_user}'.{RESET}")
            nginx_group = service_user
        Path(data_root).mkdir(parents=True, exist_ok=True)
        run_cmd(["chown", "-R", f"{service_user}:{nginx_group}", data_root])
        run_cmd(["chmod", "-R", "g+rx", data_root])
        Path(static_root).mkdir(parents=True, exist_ok=True)
        run_cmd(["chown", "-R", f"{service_user}:{nginx_group}", static_root])
        run_cmd(["chmod", "-R", "g+rx", static_root])

        print_step("Database")
        if not skip_migrate:
            run_cmd(
                [str(venv_python), str(repo_root / "document_refinery" / "manage.py"), "migrate"],
                required=True,
            )
        if ask_user("Collect static files now?", default=True):
            run_cmd(
                [str(venv_python), str(repo_root / "document_refinery" / "manage.py"), "collectstatic", "--noinput"],
                required=True,
            )
        if ask_user("Create Django superuser now? (use email as username)", default=False):
            admin_email = get_input("Admin email (used as username)")
            cmd = [
                str(venv_python),
                str(repo_root / "document_refinery" / "manage.py"),
                "createsuperuser",
                "--username",
                admin_email,
                "--email",
                admin_email,
            ]
            run_cmd(cmd)

        print_step("Systemd Services")
        gunicorn_service = f"""[Unit]
Description=DocumentRefinery Gunicorn
After=network.target

[Service]
User={service_user}
Group={nginx_group}
WorkingDirectory={project_dir}
EnvironmentFile={env_path}
RuntimeDirectory=document_refinery
RuntimeDirectoryMode=0755
ExecStart={venv_bin}/gunicorn \\
    --workers 3 \\
    --bind unix:{socket_path} \\
    config.wsgi:application
UMask=007
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
ReadWritePaths={data_root} /run/document_refinery
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
Group={nginx_group}
WorkingDirectory={project_dir}
EnvironmentFile={env_path}
ExecStart={venv_bin}/celery -A config worker --loglevel=INFO
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
ReadWritePaths={data_root}
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
Group={nginx_group}
WorkingDirectory={project_dir}
EnvironmentFile={env_path}
ExecStart={venv_bin}/celery -A config beat --loglevel=INFO
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
ReadWritePaths={data_root}
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
                if not ask_user(
                    f"{unit_path} exists. Overwrite?", default=False if resume else False
                ):
                    continue
            write_file(unit_path, content)

        run_cmd(["systemctl", "daemon-reload"], required=True)
        run_cmd(["systemctl", "enable", "--now", "gunicorn.service"], required=True)
        run_cmd(["systemctl", "enable", "--now", "celery-worker.service"], required=True)
        if ask_user("Enable celery-beat.service?", default=False):
            run_cmd(["systemctl", "enable", "--now", "celery-beat.service"], required=True)

    print_step("Nginx")
    server_name = domain_name if domain_name else "_"
    nginx_conf = f"""server {{
    listen 80;
    server_name {server_name};

    client_max_body_size 60m;

    location /static/ {{
        alias {static_root}/;
    }}

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
        if ask_user(f"{nginx_path} exists. Overwrite?", default=False if resume else False):
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
    run_cmd(["nginx", "-t"], required=True)
    run_cmd(["systemctl", "reload", "nginx"], required=True)

    if not only_nginx:
        print_step("Firewall")
        if shutil.which("ufw") and ask_user("Configure UFW?", default=False):
            run_cmd(["ufw", "allow", "OpenSSH"])
            run_cmd(["ufw", "allow", "22/tcp"])
            run_cmd(["ufw", "allow", "Nginx Full"])
            run_cmd("echo 'y' | ufw enable", shell=True)

        print_step("TLS")
        certbot_available = install_certbot or shutil.which("certbot")
        if domain_name and certbot_available:
            if ask_user("Request TLS certificate with certbot?", default=False if resume else True):
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
    if venv_dir:
        print(f"Venv: {venv_dir}")
    print(f"Data root: {data_root}")


if __name__ == "__main__":
    main()
