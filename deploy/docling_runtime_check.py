#!/usr/bin/env python3
"""Docling upgrade diagnostics for DocumentRefinery servers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "document_refinery"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class Reporter:
    def __init__(self, json_output: bool = False):
        self.json_output = json_output
        self.items: list[dict[str, str]] = []

    def add(self, level: str, check: str, message: str) -> None:
        self.items.append({"level": level, "check": check, "message": message})
        if not self.json_output:
            print(f"[{level.upper()}] {check}: {message}")

    def ok(self, check: str, message: str) -> None:
        self.add("ok", check, message)

    def warn(self, check: str, message: str) -> None:
        self.add("warn", check, message)

    def fail(self, check: str, message: str) -> None:
        self.add("fail", check, message)

    def finish(self) -> int:
        failures = sum(1 for item in self.items if item["level"] == "fail")
        warnings = sum(1 for item in self.items if item["level"] == "warn")
        if self.json_output:
            print(json.dumps({"failures": failures, "warnings": warnings, "items": self.items}, indent=2))
        elif failures or warnings:
            print(f"Summary: {failures} failure(s), {warnings} warning(s)")
        else:
            print("Summary: all checks passed")
        return 1 if failures else 0


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def set_docling_runtime_defaults() -> None:
    data_root = Path(os.environ.get("DATA_ROOT", "/var/lib/docling_service"))
    os.environ.setdefault("DOCLING_DEVICE", "cpu")
    os.environ.setdefault("DOCLING_NUM_THREADS", "2")
    os.environ.setdefault("DOCLING_ALLOWED_OCR_ENGINES", "auto,rapidocr")
    os.environ.setdefault("HF_HOME", str(data_root / "hf_cache"))
    os.environ.setdefault("DOCLING_CACHE_DIR", str(data_root / "docling_cache"))
    os.environ.setdefault("DOCLING_ARTIFACTS_PATH", str(data_root / "docling_artifacts"))


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def check_package(reporter: Reporter, name: str, expected: str, predicate) -> None:
    installed = package_version(name)
    if installed is None:
        reporter.fail(f"package:{name}", f"not installed; expected {expected}")
    elif predicate(installed):
        reporter.ok(f"package:{name}", f"{installed} ({expected})")
    else:
        reporter.fail(f"package:{name}", f"{installed}; expected {expected}")


def check_environment(reporter: Reporter) -> None:
    data_root = Path(os.environ.get("DATA_ROOT", "/var/lib/docling_service"))
    hf_home = Path(os.environ.get("HF_HOME", str(data_root / "hf_cache")))
    docling_cache_dir = Path(
        os.environ.get("DOCLING_CACHE_DIR", str(data_root / "docling_cache"))
    )
    artifacts_path = Path(
        os.environ.get("DOCLING_ARTIFACTS_PATH", str(data_root / "docling_artifacts"))
    )
    docling_device = os.environ.get("DOCLING_DEVICE")
    docling_threads = os.environ.get("DOCLING_NUM_THREADS")
    allowed_ocr_engines = os.environ.get("DOCLING_ALLOWED_OCR_ENGINES")
    worker_concurrency = os.environ.get("CELERY_WORKER_CONCURRENCY")
    data_root_abs = data_root.resolve()

    if docling_device:
        reporter.ok("env:DOCLING_DEVICE", docling_device)
    else:
        reporter.warn("env:DOCLING_DEVICE", "not set; recommended value is cpu")

    if docling_threads:
        reporter.ok("env:DOCLING_NUM_THREADS", docling_threads)
    else:
        reporter.warn("env:DOCLING_NUM_THREADS", "not set; recommended value is 2")

    if allowed_ocr_engines:
        reporter.ok("env:DOCLING_ALLOWED_OCR_ENGINES", allowed_ocr_engines)
    else:
        reporter.warn(
            "env:DOCLING_ALLOWED_OCR_ENGINES",
            "not set; recommended value is auto,rapidocr",
        )

    if worker_concurrency:
        reporter.ok("env:CELERY_WORKER_CONCURRENCY", worker_concurrency)
    else:
        reporter.warn("env:CELERY_WORKER_CONCURRENCY", "not set; recommended value is 1")

    if not hf_home.exists():
        reporter.warn("env:HF_HOME", f"{hf_home} does not exist yet")
    elif os.access(hf_home, os.W_OK):
        reporter.ok("env:HF_HOME", f"{hf_home} is writable")
    else:
        reporter.fail("env:HF_HOME", f"{hf_home} is not writable")
    if data_root_abs not in hf_home.resolve().parents:
        reporter.warn("env:HF_HOME", f"{hf_home} is outside DATA_ROOT")

    if not docling_cache_dir.exists():
        reporter.warn("env:DOCLING_CACHE_DIR", f"{docling_cache_dir} does not exist yet")
    elif os.access(docling_cache_dir, os.W_OK):
        reporter.ok("env:DOCLING_CACHE_DIR", f"{docling_cache_dir} is writable")
    else:
        reporter.fail("env:DOCLING_CACHE_DIR", f"{docling_cache_dir} is not writable")
    if data_root_abs not in docling_cache_dir.resolve().parents:
        reporter.warn("env:DOCLING_CACHE_DIR", f"{docling_cache_dir} is outside DATA_ROOT")

    if not artifacts_path.exists():
        reporter.warn("env:DOCLING_ARTIFACTS_PATH", f"{artifacts_path} does not exist yet")
    elif os.access(artifacts_path, os.W_OK):
        reporter.ok("env:DOCLING_ARTIFACTS_PATH", f"{artifacts_path} is writable")
    else:
        reporter.fail("env:DOCLING_ARTIFACTS_PATH", f"{artifacts_path} is not writable")
    if data_root_abs not in artifacts_path.resolve().parents:
        reporter.warn("env:DOCLING_ARTIFACTS_PATH", f"{artifacts_path} is outside DATA_ROOT")


def check_imports(reporter: Reporter) -> None:
    try:
        from docling.datamodel.base_models import InputFormat  # noqa: F401
        from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: F401
        from docling_core.types.doc import DoclingDocument  # noqa: F401

        reporter.ok("docling:imports", "core Docling imports succeeded")
    except Exception as exc:
        try:
            import docling

            location = getattr(docling, "__file__", "<unknown>")
            paths = ", ".join(str(path) for path in getattr(docling, "__path__", []))
        except Exception as docling_exc:
            location = f"<docling import failed: {type(docling_exc).__name__}: {docling_exc}>"
            paths = "<unknown>"
        reporter.fail(
            "docling:imports",
            (
                f"{type(exc).__name__}: {exc}; "
                f"docling={location}; docling_path=[{paths}]; "
                f"docling-slim={package_version('docling-slim') or 'not installed'}"
            ),
        )
        return

    try:
        from documents.profiles import PROFILE_NAMES, build_profile_pipeline_options

        for profile in PROFILE_NAMES:
            build_profile_pipeline_options(profile)
        reporter.ok("docling:profiles", f"{len(PROFILE_NAMES)} profiles validated")
    except Exception as exc:
        reporter.fail("docling:profiles", f"{type(exc).__name__}: {exc}")


def check_rapidocr_models(reporter: Reporter) -> None:
    try:
        from docling_model_warmup import (
            detect_rapidocr_backends,
            expected_docling_model_dirs,
            expected_rapidocr_model_paths,
            rapidocr_languages,
            runtime_paths,
            warm_easyocr_models,
        )
    except Exception as exc:
        reporter.fail("rapidocr:artifacts", f"warmup helper unavailable: {type(exc).__name__}: {exc}")
        return

    try:
        paths = runtime_paths()
        expected_dirs = expected_docling_model_dirs(paths["docling_artifacts_path"])
        missing_dirs = [str(path) for path in expected_dirs if not path.exists()]
        if missing_dirs:
            reporter.fail(
                "docling:artifacts",
                (
                    f"{len(missing_dirs)} missing base model directory/directories under "
                    f"{paths['docling_artifacts_path']}; run deploy/docling_model_warmup.py"
                ),
            )
        else:
            reporter.ok(
                "docling:artifacts",
                f"{len(expected_dirs)} base model directory/directories ready",
            )

        backends = detect_rapidocr_backends()
        languages = rapidocr_languages()
        if not backends:
            reporter.fail(
                "rapidocr:backend",
                (
                    "no default RapidOCR backend importable; install onnxruntime or set "
                    "DOCLING_RAPIDOCR_BACKENDS to an explicitly supported backend"
                ),
            )
            return
        expected = expected_rapidocr_model_paths(
            paths["docling_artifacts_path"],
            backends=backends,
            languages=languages,
        )
        missing = [str(path) for path in expected if not path.exists()]
        if missing:
            reporter.fail(
                "rapidocr:artifacts",
                (
                    f"{len(missing)} missing model artifact(s) under "
                    f"{paths['docling_artifacts_path']}; run deploy/docling_model_warmup.py"
                ),
            )
        else:
            reporter.ok(
                "rapidocr:artifacts",
                (
                    f"{len(expected)} model artifact(s) ready; "
                    f"backends={','.join(backends)}; languages={','.join(languages)}"
                ),
            )

        easyocr = warm_easyocr_models(
            artifacts_path=paths["docling_artifacts_path"],
            check_only=True,
        )
        if easyocr.get("status") == "skip":
            reporter.warn("easyocr:artifacts", easyocr.get("message", "EasyOCR not installed"))
        elif easyocr.get("status") == "fail":
            reporter.fail(
                "easyocr:artifacts",
                f"missing model artifacts under {easyocr.get('easyocr_dir')}",
            )
        else:
            reporter.ok("easyocr:artifacts", f"model artifacts ready under {easyocr.get('easyocr_dir')}")
    except Exception as exc:
        reporter.fail("rapidocr:artifacts", f"{type(exc).__name__}: {exc}")


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


def run_smoke(reporter: Reporter, profile: str) -> None:
    set_docling_runtime_defaults()

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from documents.profiles import build_profile_pipeline_options

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "docling-smoke.pdf"
            pdf_path.write_bytes(build_pdf())
            pipeline_options = build_profile_pipeline_options(profile)
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            result = converter.convert(str(pdf_path), max_num_pages=1, max_file_size=2_000_000)
        status = getattr(getattr(result, "status", None), "value", "success")
        errors = getattr(result, "errors", []) or []
        if status == "success":
            reporter.ok("docling:smoke", f"profile={profile}, errors={len(errors)}")
        else:
            reporter.fail("docling:smoke", f"profile={profile}, status={status}, errors={errors}")
    except Exception as exc:
        reporter.fail("docling:smoke", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check DocumentRefinery Docling runtime readiness.")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="fail if configured Docling/RapidOCR model artifacts are missing",
    )
    parser.add_argument("--smoke", action="store_true", help="run a real one-page Docling conversion")
    parser.add_argument("--profile", default="fast_text", help="profile to use for --smoke")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    set_docling_runtime_defaults()
    reporter = Reporter(json_output=args.json)

    check_package(reporter, "Django", "5.2.x LTS", lambda value: value.startswith("5.2."))
    check_package(reporter, "redis", "7.x", lambda value: value.startswith("7."))
    check_package(reporter, "docling", "2.96.1", lambda value: value == "2.96.1")
    check_package(reporter, "docling-slim", "2.96.1", lambda value: value == "2.96.1")
    check_package(reporter, "onnxruntime", "installed", lambda _value: True)
    check_environment(reporter)
    check_imports(reporter)
    if args.check_models:
        check_rapidocr_models(reporter)
    if args.smoke:
        run_smoke(reporter, args.profile)
    return reporter.finish()


if __name__ == "__main__":
    raise SystemExit(main())
