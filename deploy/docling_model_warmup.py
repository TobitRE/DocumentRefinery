#!/usr/bin/env python3
"""Prepare Docling model artifacts before production traffic runs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = "/var/lib/docling_service"
DEFAULT_RAPIDOCR_LANGUAGES = ("chinese", "english")
DEFAULT_RAPIDOCR_BACKENDS = ("onnxruntime",)
SUPPORTED_RAPIDOCR_BACKENDS = ("onnxruntime", "torch")


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


def split_csv(value: str | None, default: tuple[str, ...]) -> list[str]:
    if not value:
        return list(default)
    items = [item.strip().lower() for item in value.split(",")]
    return [item for item in items if item]


def runtime_paths(artifacts_path: str | None = None) -> dict[str, Path]:
    data_root = Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT))
    hf_home = Path(os.environ.get("HF_HOME", str(data_root / "hf_cache")))
    docling_cache_dir = Path(os.environ.get("DOCLING_CACHE_DIR", str(data_root / "docling_cache")))
    docling_artifacts_path = Path(
        artifacts_path
        or os.environ.get("DOCLING_ARTIFACTS_PATH")
        or str(data_root / "docling_artifacts")
    )
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("DOCLING_CACHE_DIR", str(docling_cache_dir))
    os.environ.setdefault("DOCLING_ARTIFACTS_PATH", str(docling_artifacts_path))
    os.environ.setdefault("DOCLING_DEVICE", "cpu")
    os.environ.setdefault("DOCLING_NUM_THREADS", "2")
    return {
        "data_root": data_root,
        "hf_home": hf_home,
        "docling_cache_dir": docling_cache_dir,
        "docling_artifacts_path": docling_artifacts_path,
        "rapidocr_dir": docling_artifacts_path / "RapidOcr",
    }


def detect_rapidocr_backends(value: str | None = None) -> list[str]:
    configured = split_csv(value or os.environ.get("DOCLING_RAPIDOCR_BACKENDS"), ())
    if configured:
        unsupported = [item for item in configured if item not in SUPPORTED_RAPIDOCR_BACKENDS]
        if unsupported:
            raise ValueError(
                "Unsupported RapidOCR backend(s): "
                + ", ".join(unsupported)
                + ". Supported values are: "
                + ", ".join(SUPPORTED_RAPIDOCR_BACKENDS)
                + "."
            )
        return configured

    return [
        backend
        for backend in DEFAULT_RAPIDOCR_BACKENDS
        if importlib.util.find_spec(backend) is not None
    ]


def rapidocr_languages(value: str | None = None) -> list[str]:
    languages = split_csv(
        value or os.environ.get("DOCLING_RAPIDOCR_LANGUAGES"),
        DEFAULT_RAPIDOCR_LANGUAGES,
    )
    normalized: list[str] = []
    for language in languages:
        if language in {"en", "eng", "english"}:
            normalized.append("english")
        else:
            normalized.append("chinese")
    return list(dict.fromkeys(normalized))


def ensure_writable_dir(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    writable = os.access(path, os.W_OK)
    return {
        "path": str(path),
        "exists": path.exists(),
        "writable": writable,
        "status": "ok" if writable else "fail",
    }


def _rapidocr_model_class():
    from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel

    return RapidOcrModel


def expected_docling_model_dirs(artifacts_path: Path) -> list[Path]:
    from docling.datamodel.pipeline_options import LayoutOptions
    from docling.models.stages.table_structure.table_structure_model import (
        TableStructureModel,
    )

    return [
        artifacts_path / LayoutOptions().model_spec.model_repo_folder,
        artifacts_path / TableStructureModel._model_repo_folder,  # noqa: SLF001
    ]


def warm_docling_base_models(
    *,
    artifacts_path: Path,
    force: bool = False,
    progress: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    expected = expected_docling_model_dirs(artifacts_path)
    missing_before = [str(path) for path in expected if not path.exists()]

    if not check_only:
        from docling.utils.model_downloader import download_models

        download_models(
            output_dir=artifacts_path,
            force=force,
            progress=progress,
            with_layout=True,
            with_tableformer=True,
            with_tableformer_v2=False,
            with_code_formula=False,
            with_picture_classifier=False,
            with_smolvlm=False,
            with_granitedocling=False,
            with_granitedocling_mlx=False,
            with_granitedocling_2stage=False,
            with_smoldocling=False,
            with_smoldocling_mlx=False,
            with_granite_vision=False,
            with_granite_chart_extraction=False,
            with_granite_chart_extraction_v4=False,
            with_rapidocr=False,
            with_easyocr=False,
        )

    missing_after = [str(path) for path in expected if not path.exists()]
    return {
        "status": "ok" if not missing_after else "fail",
        "artifacts_path": str(artifacts_path),
        "expected_dirs": [str(path) for path in expected],
        "missing_before": missing_before,
        "missing_after": missing_after,
    }


def expected_rapidocr_model_paths(
    artifacts_path: Path,
    *,
    backends: list[str],
    languages: list[str],
) -> list[Path]:
    RapidOcrModel = _rapidocr_model_class()
    expected: list[Path] = []
    for language in languages:
        model_sets = RapidOcrModel._models_by_language.get(language)  # noqa: SLF001
        if not model_sets:
            continue
        for backend in backends:
            model_set = model_sets.get(backend)
            if not model_set:
                continue
            for details in model_set.values():
                expected.append(artifacts_path / "RapidOcr" / details["path"])
    return expected


def warm_rapidocr_models(
    *,
    artifacts_path: Path,
    backends: list[str],
    languages: list[str],
    force: bool = False,
    progress: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    rapidocr_dir = artifacts_path / "RapidOcr"
    rapidocr_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(rapidocr_dir, os.W_OK):
        raise RuntimeError(f"RapidOCR artifact directory is not writable: {rapidocr_dir}")

    expected = expected_rapidocr_model_paths(
        artifacts_path,
        backends=backends,
        languages=languages,
    )
    missing_before = [str(path) for path in expected if not path.exists()]

    downloaded: list[dict[str, str]] = []
    if not check_only:
        RapidOcrModel = _rapidocr_model_class()
        for backend in backends:
            for language in languages:
                RapidOcrModel.download_models(
                    backend=backend,
                    local_dir=rapidocr_dir,
                    force=force,
                    progress=progress,
                    lang=language,
                )
                downloaded.append({"backend": backend, "language": language})

    missing_after = [str(path) for path in expected if not path.exists()]
    return {
        "status": "ok" if not missing_after else "fail",
        "artifacts_path": str(artifacts_path),
        "rapidocr_dir": str(rapidocr_dir),
        "backends": backends,
        "languages": languages,
        "expected_count": len(expected),
        "missing_before": missing_before,
        "missing_after": missing_after,
        "downloaded": downloaded,
    }


def warm_easyocr_models(
    *,
    artifacts_path: Path,
    force: bool = False,
    progress: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    if importlib.util.find_spec("easyocr") is None:
        return {
            "status": "skip",
            "message": "EasyOCR is not installed.",
        }

    easyocr_dir = artifacts_path / "EasyOcr"
    missing_before = not easyocr_dir.exists() or not any(easyocr_dir.iterdir())

    if not check_only:
        from docling.models.stages.ocr.easyocr_model import EasyOcrModel

        EasyOcrModel.download_models(
            local_dir=easyocr_dir,
            force=force,
            progress=progress,
        )

    missing_after = not easyocr_dir.exists() or not any(easyocr_dir.iterdir())
    return {
        "status": "ok" if not missing_after else "fail",
        "artifacts_path": str(artifacts_path),
        "easyocr_dir": str(easyocr_dir),
        "missing_before": missing_before,
        "missing_after": missing_after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and verify Docling layout, table, and RapidOCR model artifacts."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--artifacts-path", default=None)
    parser.add_argument(
        "--backends",
        default=None,
        help="Comma-separated RapidOCR backends. Defaults to installed onnxruntime/torch.",
    )
    parser.add_argument(
        "--languages",
        default=None,
        help="Comma-separated RapidOCR languages. Defaults to chinese,english.",
    )
    parser.add_argument("--force", action="store_true", help="download even if files exist")
    parser.add_argument("--check-only", action="store_true", help="verify files without downloading")
    parser.add_argument("--progress", action="store_true", help="show Docling download progress")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    paths = runtime_paths(args.artifacts_path)
    payload: dict[str, Any] = {
        "paths": {key: str(value) for key, value in paths.items()},
        "directories": {
            "hf_home": ensure_writable_dir(paths["hf_home"]),
            "docling_cache_dir": ensure_writable_dir(paths["docling_cache_dir"]),
            "docling_artifacts_path": ensure_writable_dir(paths["docling_artifacts_path"]),
        },
    }

    try:
        payload["docling_models"] = warm_docling_base_models(
            artifacts_path=paths["docling_artifacts_path"],
            force=args.force,
            progress=args.progress,
            check_only=args.check_only,
        )
    except Exception as exc:
        payload["docling_models"] = {
            "status": "fail",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

    try:
        backends = detect_rapidocr_backends(args.backends)
        languages = rapidocr_languages(args.languages)
        payload["rapidocr"] = {
            "backends": backends,
            "languages": languages,
        }
        if backends:
            payload["rapidocr"] = warm_rapidocr_models(
                artifacts_path=paths["docling_artifacts_path"],
                backends=backends,
                languages=languages,
                force=args.force,
                progress=args.progress,
                check_only=args.check_only,
            )
        else:
            payload["rapidocr"]["status"] = "fail"
            payload["rapidocr"]["message"] = (
                "No default RapidOCR backend is importable. Install onnxruntime or set "
                "DOCLING_RAPIDOCR_BACKENDS to an explicitly supported backend."
            )
    except Exception as exc:
        payload["rapidocr"] = {
            "status": "fail",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

    try:
        payload["easyocr"] = warm_easyocr_models(
            artifacts_path=paths["docling_artifacts_path"],
            force=args.force,
            progress=args.progress,
            check_only=args.check_only,
        )
    except Exception as exc:
        payload["easyocr"] = {
            "status": "fail",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

    failures = [
        item
        for section in ("directories",)
        for item in payload.get(section, {}).values()
        if item.get("status") == "fail"
    ]
    docling_models_status = payload.get("docling_models", {}).get("status")
    if docling_models_status == "fail":
        failures.append(payload["docling_models"])
    rapidocr_status = payload.get("rapidocr", {}).get("status")
    if rapidocr_status == "fail":
        failures.append(payload["rapidocr"])
    easyocr_status = payload.get("easyocr", {}).get("status")
    if easyocr_status == "fail":
        failures.append(payload["easyocr"])

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"DOCLING_ARTIFACTS_PATH={paths['docling_artifacts_path']}")
        print(f"DOCLING_CACHE_DIR={paths['docling_cache_dir']}")
        print(f"HF_HOME={paths['hf_home']}")
        docling_models = payload.get("docling_models", {})
        print(f"Docling base model warmup: {docling_models.get('status', 'unknown')}")
        if docling_models.get("missing_after"):
            print("Missing Docling model directories:")
            for path in docling_models["missing_after"]:
                print(f"- {path}")
        rapidocr = payload.get("rapidocr", {})
        status = rapidocr.get("status", "unknown")
        print(f"RapidOCR model warmup: {status}")
        if rapidocr.get("message"):
            print(rapidocr["message"])
        if rapidocr.get("missing_after"):
            print("Missing RapidOCR artifacts:")
            for path in rapidocr["missing_after"]:
                print(f"- {path}")
        easyocr = payload.get("easyocr", {})
        easyocr_status = easyocr.get("status", "unknown")
        print(f"EasyOCR model warmup: {easyocr_status}")
        if easyocr.get("message"):
            print(easyocr["message"])
        if easyocr.get("missing_after"):
            print(f"Missing EasyOCR artifacts in {easyocr.get('easyocr_dir')}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
