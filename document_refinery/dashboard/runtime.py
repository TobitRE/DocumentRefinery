from __future__ import annotations

import importlib.util
import multiprocessing
import os
import platform
import queue
import shutil
import subprocess
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from celery import current_app
from django.conf import settings
from django.utils import timezone


RUNTIME_CACHE_TTL = 5
SMOKE_TIMEOUT_SECONDS = 30
SMOKE_RATE_LIMIT_SECONDS = 30

_RUNTIME_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
SMOKE_LOCK_FILENAME = "runtime-smoke.lock"
SMOKE_RATE_FILENAME = "runtime-smoke.last"


def _smoke_state_dir() -> Path:
    state_dir = Path(settings.DATA_ROOT) / "runtime"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _smoke_lock_path() -> Path:
    return _smoke_state_dir() / SMOKE_LOCK_FILENAME


def _smoke_rate_path() -> Path:
    return _smoke_state_dir() / SMOKE_RATE_FILENAME


def _read_smoke_last_started() -> float:
    try:
        return float(_smoke_rate_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0


def _write_smoke_last_started(started: float) -> None:
    try:
        _smoke_rate_path().write_text(str(started), encoding="utf-8")
    except OSError:
        pass


def _acquire_smoke_lock(now: float) -> Path | None:
    lock_path = _smoke_lock_path()
    try:
        if lock_path.exists() and now - lock_path.stat().st_mtime > SMOKE_TIMEOUT_SECONDS + 10:
            lock_path.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    try:
        os.write(fd, str(now).encode("utf-8"))
    finally:
        os.close(fd)
    return lock_path


def _release_smoke_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass


def _package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _status_package(package_name: str, expected: str, predicate=None) -> dict[str, Any]:
    installed = _package_version(package_name)
    if installed is None:
        return {
            "name": package_name,
            "version": None,
            "expected": expected,
            "status": "fail",
            "message": "not installed",
        }
    if predicate is None or predicate(installed):
        return {
            "name": package_name,
            "version": installed,
            "expected": expected,
            "status": "ok",
            "message": installed,
        }
    return {
        "name": package_name,
        "version": installed,
        "expected": expected,
        "status": "fail",
        "message": f"{installed}; expected {expected}",
    }


def _disk_usage(path: Path) -> dict[str, int | float] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def _path_check(label: str, path: Path, *, must_exist: bool) -> dict[str, Any]:
    exists = path.exists()
    writable = os.access(path, os.W_OK) if exists else False
    status = "ok"
    message = "available"
    if must_exist and not exists:
        status = "fail"
        message = "missing"
    elif not exists:
        status = "warn"
        message = "missing"
    elif not writable:
        status = "fail"
        message = "not writable"
    return {
        "label": label,
        "path": str(path),
        "exists": exists,
        "writable": writable,
        "disk": _disk_usage(path) if exists else None,
        "status": status,
        "message": message,
    }


def _tool_check(name: str, command: list[str] | None = None) -> dict[str, Any]:
    executable = shutil.which(name)
    payload: dict[str, Any] = {
        "name": name,
        "path": executable,
        "status": "ok" if executable else "warn",
        "message": "installed" if executable else "not installed",
    }
    if executable and command:
        try:
            result = subprocess.run(
                [executable, *command],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            first_line = (result.stdout or result.stderr or "").splitlines()[:1]
            payload["version_output"] = first_line[0] if first_line else ""
            if result.returncode != 0:
                payload["status"] = "warn"
                payload["message"] = f"command returned {result.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            payload["status"] = "warn"
            payload["message"] = f"version check failed: {exc}"
    return payload


def _import_check(label: str, module_name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(module_name) is not None
    return {
        "label": label,
        "module": module_name,
        "available": available,
        "status": "ok" if available else "warn",
        "message": "importable" if available else "not importable",
    }


def _rapidocr_artifact_check(artifacts_path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("rapidocr") is None:
        return {
            "label": "RapidOCR artifacts",
            "status": "warn",
            "message": "rapidocr is not importable",
            "path": str(artifacts_path),
        }

    configured_backends = [
        item.strip().lower()
        for item in os.environ.get("DOCLING_RAPIDOCR_BACKENDS", "onnxruntime").split(",")
        if item.strip()
    ]
    supported_backends = {"onnxruntime", "torch"}
    unsupported_backends = sorted(set(configured_backends) - supported_backends)
    if unsupported_backends:
        return {
            "label": "RapidOCR artifacts",
            "status": "fail",
            "message": "unsupported backend(s): " + ", ".join(unsupported_backends),
            "path": str(artifacts_path),
        }

    backends = [
        backend
        for backend in configured_backends
        if importlib.util.find_spec(backend) is not None
    ]
    if not backends:
        return {
            "label": "RapidOCR artifacts",
            "status": "fail",
            "message": "configured RapidOCR backend is not importable: "
            + ", ".join(configured_backends or ["onnxruntime"]),
            "path": str(artifacts_path),
        }

    try:
        from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel

        expected = []
        for language in ("chinese", "english"):
            model_sets = RapidOcrModel._models_by_language.get(language)  # noqa: SLF001
            if not model_sets:
                continue
            for backend in backends:
                model_set = model_sets.get(backend)
                if not model_set:
                    continue
                for details in model_set.values():
                    expected.append(artifacts_path / "RapidOcr" / details["path"])
        missing = [str(path) for path in expected if not path.exists()]
        return {
            "label": "RapidOCR artifacts",
            "status": "ok" if not missing else "fail",
            "message": "model artifacts ready" if not missing else f"{len(missing)} missing",
            "path": str(artifacts_path),
            "backends": backends,
            "expected_count": len(expected),
            "missing_count": len(missing),
        }
    except Exception as exc:
        return {
            "label": "RapidOCR artifacts",
            "status": "fail",
            "message": f"{type(exc).__name__}: {exc}",
            "path": str(artifacts_path),
        }


def _docling_base_artifact_check(artifacts_path: Path) -> dict[str, Any]:
    try:
        from docling.datamodel.pipeline_options import LayoutOptions
        from docling.models.stages.table_structure.table_structure_model import (
            TableStructureModel,
        )

        expected = [
            artifacts_path / LayoutOptions().model_spec.model_repo_folder,
            artifacts_path / TableStructureModel._model_repo_folder,  # noqa: SLF001
        ]
        missing = [str(path) for path in expected if not path.exists()]
        return {
            "label": "Docling base artifacts",
            "status": "ok" if not missing else "fail",
            "message": "base model artifacts ready" if not missing else f"{len(missing)} missing",
            "path": str(artifacts_path),
            "expected_count": len(expected),
            "missing_count": len(missing),
        }
    except Exception as exc:
        return {
            "label": "Docling base artifacts",
            "status": "fail",
            "message": f"{type(exc).__name__}: {exc}",
            "path": str(artifacts_path),
        }


def _easyocr_artifact_check(artifacts_path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("easyocr") is None:
        return {
            "label": "EasyOCR artifacts",
            "status": "warn",
            "message": "easyocr is not importable",
            "path": str(artifacts_path),
        }
    easyocr_dir = artifacts_path / "EasyOcr"
    ready = easyocr_dir.exists() and any(easyocr_dir.iterdir())
    return {
        "label": "EasyOCR artifacts",
        "status": "ok" if ready else "fail",
        "message": "model artifacts ready" if ready else "missing",
        "path": str(easyocr_dir),
    }


def _celery_status() -> dict[str, Any]:
    broker_ok = False
    try:
        current_app.connection().ensure_connection(max_retries=1)
        broker_ok = True
    except Exception:
        broker_ok = False

    try:
        inspect = current_app.control.inspect()
        ping = inspect.ping() or {}
        stats = inspect.stats() or {}
        active = inspect.active() or {}
    except Exception:
        ping, stats, active = {}, {}, {}

    workers = []
    active_count = 0
    for hostname, info in (stats or {}).items():
        worker_active = len((active or {}).get(hostname, []))
        active_count += worker_active
        workers.append(
            {
                "hostname": hostname,
                "active_tasks": worker_active,
                "pool": (info.get("pool") or {}).get("implementation"),
                "concurrency": (info.get("pool") or {}).get("max-concurrency"),
            }
        )

    return {
        "broker": {"status": "ok" if broker_ok else "fail", "connected": broker_ok},
        "workers_online": len(ping or {}),
        "workers": workers,
        "active_tasks": active_count,
        "status": "ok" if broker_ok and ping else "warn" if broker_ok else "fail",
    }


def _summarize(payload: dict[str, Any]) -> dict[str, Any]:
    statuses = []

    def collect(value):
        if isinstance(value, dict):
            status = value.get("status")
            if status in {"ok", "warn", "fail"}:
                statuses.append(status)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return {
        "ok": statuses.count("ok"),
        "warnings": statuses.count("warn"),
        "failures": statuses.count("fail"),
    }


def runtime_diagnostics_payload(*, force_refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not force_refresh
        and _RUNTIME_CACHE["payload"]
        and now - float(_RUNTIME_CACHE["ts"]) < RUNTIME_CACHE_TTL
    ):
        return _RUNTIME_CACHE["payload"]

    data_root = Path(getattr(settings, "DATA_ROOT", "/var/lib/docling_service"))
    hf_home = Path(getattr(settings, "HF_HOME", os.environ.get("HF_HOME", data_root / "hf_cache")))
    docling_cache_dir = Path(
        getattr(
            settings,
            "DOCLING_CACHE_DIR",
            os.environ.get("DOCLING_CACHE_DIR", data_root / "docling_cache"),
        )
    )
    artifacts_path = Path(
        getattr(
            settings,
            "DOCLING_ARTIFACTS_PATH",
            os.environ.get("DOCLING_ARTIFACTS_PATH", data_root / "docling_artifacts"),
        )
    )

    payload = {
        "timestamp": timezone.now().isoformat(),
        "packages": [
            _status_package("Django", "5.2.x LTS", lambda value: value.startswith("5.2.")),
            _status_package("redis", "7.x", lambda value: value.startswith("7.")),
            _status_package("docling", "2.96.1", lambda value: value == "2.96.1"),
            _status_package("docling-core", "installed"),
            _status_package("docling-parse", "installed"),
            _status_package("onnxruntime", "installed"),
        ],
        "environment": {
            "DOCLING_DEVICE": str(getattr(settings, "DOCLING_DEVICE", "")),
            "DOCLING_NUM_THREADS": str(getattr(settings, "DOCLING_NUM_THREADS", "")),
            "DOCLING_ALLOWED_OCR_ENGINES": str(
                getattr(settings, "DOCLING_ALLOWED_OCR_ENGINES", "")
            ),
            "HF_HOME": str(hf_home),
            "DOCLING_CACHE_DIR": str(docling_cache_dir),
            "DOCLING_ARTIFACTS_PATH": str(artifacts_path),
            "DATA_ROOT": str(data_root),
            "CELERY_WORKER_CONCURRENCY": str(
                getattr(settings, "CELERY_WORKER_CONCURRENCY", "")
            ),
            "platform": platform.platform(),
        },
        "filesystem": {
            "data_root": _path_check("DATA_ROOT", data_root, must_exist=True),
            "hf_home": _path_check("HF_HOME", hf_home, must_exist=False),
            "docling_cache_dir": _path_check(
                "DOCLING_CACHE_DIR",
                docling_cache_dir,
                must_exist=False,
            ),
            "docling_artifacts_path": _path_check(
                "DOCLING_ARTIFACTS_PATH",
                artifacts_path,
                must_exist=False,
            ),
            "root": {
                "label": "/",
                "path": "/",
                "disk": _disk_usage(Path("/")),
                "status": "ok",
            },
        },
        "tools": {
            "ffmpeg": _tool_check("ffmpeg", ["-version"]),
            "tesseract": _tool_check("tesseract", ["--version"]),
        },
        "ocr_backends": {
            "docling_base_artifacts": _docling_base_artifact_check(artifacts_path),
            "rapidocr": _import_check("RapidOCR", "rapidocr"),
            "onnxruntime": _import_check("ONNX Runtime", "onnxruntime"),
            "rapidocr_artifacts": _rapidocr_artifact_check(artifacts_path),
            "easyocr": _import_check("EasyOCR", "easyocr"),
            "easyocr_artifacts": _easyocr_artifact_check(artifacts_path),
            "pytesseract": _import_check("pytesseract", "pytesseract"),
            "tesseract_cli": {
                "label": "Tesseract CLI",
                "available": shutil.which("tesseract") is not None,
                "status": "ok" if shutil.which("tesseract") else "warn",
            },
            "mac": {
                "label": "macOS Vision OCR",
                "available": platform.system() == "Darwin",
                "status": "ok" if platform.system() == "Darwin" else "warn",
            },
        },
        "celery": _celery_status(),
    }
    payload["summary"] = _summarize(payload)
    payload["warnings"] = [
        f"{item.get('name') or item.get('label')}: {item.get('message') or item.get('status')}"
        for section in ("packages",)
        for item in payload[section]
        if item.get("status") in {"warn", "fail"}
    ]

    _RUNTIME_CACHE["payload"] = payload
    _RUNTIME_CACHE["ts"] = now
    return payload


def _build_pdf() -> bytes:
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


def _smoke_worker(profile: str, result_queue) -> None:
    try:
        os.environ.setdefault("DOCLING_DEVICE", str(getattr(settings, "DOCLING_DEVICE", "cpu")))
        os.environ.setdefault(
            "DOCLING_NUM_THREADS", str(getattr(settings, "DOCLING_NUM_THREADS", "2"))
        )
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from documents.profiles import build_profile_pipeline_options

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "runtime-smoke.pdf"
            pdf_path.write_bytes(_build_pdf())
            pipeline_options = build_profile_pipeline_options(profile)
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            result = converter.convert(str(pdf_path), max_num_pages=1, max_file_size=2_000_000)
        status = getattr(getattr(result, "status", None), "value", "success")
        errors = [str(item)[:500] for item in (getattr(result, "errors", []) or [])]
        result_queue.put(
            {
                "status": "ok" if str(status).lower() == "success" else "fail",
                "docling_status": str(status),
                "profile": profile,
                "errors": errors,
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "status": "fail",
                "profile": profile,
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            }
        )


def run_runtime_smoke(profile: str = "fast_text") -> dict[str, Any]:
    now = time.time()
    last_started = _read_smoke_last_started()
    if now - last_started < SMOKE_RATE_LIMIT_SECONDS:
        return {
            "status": "rate_limited",
            "message": "Runtime smoke was run too recently.",
            "retry_after_seconds": int(SMOKE_RATE_LIMIT_SECONDS - (now - last_started)),
        }
    lock_path = _acquire_smoke_lock(now)
    if not lock_path:
        return {"status": "busy", "message": "Runtime smoke is already running."}

    try:
        _write_smoke_last_started(now)
        ctx = multiprocessing.get_context()
        result_queue = ctx.Queue()
        process = ctx.Process(target=_smoke_worker, args=(profile, result_queue))
        start = time.monotonic()
        process.start()
        process.join(SMOKE_TIMEOUT_SECONDS)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if process.is_alive():
            process.terminate()
            process.join(2)
            return {
                "status": "timeout",
                "profile": profile,
                "elapsed_ms": elapsed_ms,
                "timeout_seconds": SMOKE_TIMEOUT_SECONDS,
            }
        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            result = {
                "status": "fail",
                "profile": profile,
                "message": f"Smoke process exited without result (exit={process.exitcode}).",
            }
        result["elapsed_ms"] = elapsed_ms
        return result
    finally:
        _release_smoke_lock(lock_path)
