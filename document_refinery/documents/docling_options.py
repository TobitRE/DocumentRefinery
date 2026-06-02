from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError

from .profiles import (
    get_profile_definition,
    build_pdf_pipeline_options_from_dict,
    profile_catalog,
)


ALLOWED_EXPORTS = ("markdown", "text", "doctags", "chunks_json", "figures_zip")
ALWAYS_GENERATED_EXPORTS = ("docling_json",)
OCR_ENGINES = ("auto", "rapidocr", "easyocr", "tesseract", "tesseract_cli", "mac")
STRUCTURED_OPTION_KEYS = {
    "max_num_pages",
    "max_file_size",
    "exports",
    "do_ocr",
    "ocr",
    "ocr_engine",
    "ocr_languages",
    "ocr_options",
    "force_full_page_ocr",
    "do_table_structure",
    "generate_parsed_pages",
    "generate_picture_images",
    "images_scale",
}
PIPELINE_OPTION_KEYS = {
    "do_ocr",
    "do_table_structure",
    "generate_parsed_pages",
    "generate_picture_images",
    "images_scale",
    "ocr_options",
}
UNSUPPORTED_DOC_OPTION_KEYS = {
    "do_picture_description": "Picture description is not supported yet.",
    "do_picture_classification": "Picture classification is not supported yet.",
}
DOC_OPTION_SCHEMA = (
    {"key": "max_num_pages", "type": "integer", "minimum": 0},
    {"key": "max_file_size", "type": "integer", "minimum": 0},
    {"key": "do_ocr", "type": "boolean"},
    {"key": "ocr_engine", "type": "choice", "choices": list(OCR_ENGINES)},
    {"key": "ocr_languages", "type": "string_list"},
    {"key": "force_full_page_ocr", "type": "boolean"},
    {"key": "do_table_structure", "type": "boolean"},
    {"key": "generate_parsed_pages", "type": "boolean"},
    {"key": "generate_picture_images", "type": "boolean"},
    {"key": "images_scale", "type": "number", "minimum": 0},
    {"key": "exports", "type": "choice_list", "choices": list(ALLOWED_EXPORTS)},
)


def _merge_dicts(base: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _require_bool(key: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ValidationError(f"{key} must be a boolean.")


def _require_non_negative_int(key: str, value: Any) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValidationError(f"{key} must be a non-negative integer.")


def _require_string_list(key: str, value: Any) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(f"{key} must be a list of strings.")


def _require_non_negative_number(key: str, value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{key} must be a non-negative number.")


def normalize_docling_options(options: dict | None) -> tuple[dict, list[str]]:
    if options in (None, {}):
        return {}, []
    if not isinstance(options, dict):
        raise ValidationError("Docling options must be a JSON object.")

    normalized = deepcopy(options)
    warnings: list[str] = []

    for key, value in list(normalized.items()):
        if key in UNSUPPORTED_DOC_OPTION_KEYS:
            if value is False:
                warnings.append(
                    f"{key} is retained as a disabled compatibility option; enabling it is not supported yet."
                )
                continue
            raise ValidationError(
                f"{key} is not supported by this service yet. {UNSUPPORTED_DOC_OPTION_KEYS[key]}"
            )
        if key in ("max_num_pages", "max_file_size"):
            _require_non_negative_int(key, value)
        elif key == "exports":
            _require_string_list(key, value)
            unsupported = [item for item in value if item not in ALLOWED_EXPORTS]
            if unsupported:
                raise ValidationError(
                    "Unsupported exports: "
                    + ", ".join(unsupported)
                    + ". Supported values are: "
                    + ", ".join(ALLOWED_EXPORTS)
                    + "."
                )
        elif key in ("do_ocr", "ocr", "force_full_page_ocr", "do_table_structure"):
            _require_bool(key, value)
        elif key in ("generate_parsed_pages", "generate_picture_images"):
            _require_bool(key, value)
        elif key == "ocr_languages":
            _require_string_list(key, value)
        elif key == "ocr_engine":
            if value not in OCR_ENGINES:
                raise ValidationError(
                    "ocr_engine must be one of: " + ", ".join(OCR_ENGINES) + "."
                )
        elif key == "images_scale":
            _require_non_negative_number(key, value)
        elif key == "ocr_options":
            if not isinstance(value, dict):
                raise ValidationError("ocr_options must be a JSON object.")
            kind = value.get("kind", "auto")
            if kind not in OCR_ENGINES:
                raise ValidationError(
                    "ocr_options.kind must be one of: " + ", ".join(OCR_ENGINES) + "."
                )
            if "lang" in value:
                _require_string_list("ocr_options.lang", value["lang"])
            if "force_full_page_ocr" in value:
                _require_bool("ocr_options.force_full_page_ocr", value["force_full_page_ocr"])
        elif key not in STRUCTURED_OPTION_KEYS and key not in PIPELINE_OPTION_KEYS:
            warnings.append(f"Unknown Docling option retained for JSON fallback: {key}.")

    if "ocr" in normalized and "do_ocr" not in normalized:
        normalized["do_ocr"] = normalized["ocr"]
        warnings.append("Legacy option 'ocr' was mapped to 'do_ocr'.")
    if "ocr_languages" in normalized:
        ocr_options = dict(normalized.get("ocr_options") or {})
        if "lang" not in ocr_options:
            ocr_options["lang"] = list(normalized["ocr_languages"])
            warnings.append("Legacy option 'ocr_languages' was mapped to 'ocr_options.lang'.")
        normalized["ocr_options"] = ocr_options
    if "ocr_engine" in normalized:
        ocr_options = dict(normalized.get("ocr_options") or {})
        if "kind" not in ocr_options:
            ocr_options["kind"] = normalized["ocr_engine"]
        normalized["ocr_options"] = ocr_options
    if "force_full_page_ocr" in normalized:
        ocr_options = dict(normalized.get("ocr_options") or {})
        if "force_full_page_ocr" not in ocr_options:
            ocr_options["force_full_page_ocr"] = normalized["force_full_page_ocr"]
        normalized["ocr_options"] = ocr_options

    return normalized, warnings


def validate_docling_options_payload(options: dict | None) -> None:
    normalize_docling_options(options)


def validate_effective_options(options: dict | None) -> None:
    normalize_docling_options(options)


def apply_profile_overrides(options: dict | None, profile: str | None) -> dict:
    merged = deepcopy(options or {})
    definition = get_profile_definition(profile)
    if not definition:
        return merged
    for key, value in (definition.get("pipeline_options") or {}).items():
        if key == "ocr_options" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(value, merged[key])
        else:
            merged[key] = deepcopy(value)
    exports = definition.get("exports")
    if exports:
        merged["exports"] = list(exports)
    return merged


def resolve_effective_options(api_key, request_options: dict | None, profile: str | None) -> dict:
    layers = []
    warnings: list[str] = []
    effective: dict[str, Any] = {}

    layer_sources = [
        ("settings", getattr(settings, "DOC_DEFAULT_OPTIONS", None) or {}),
        ("tenant", getattr(api_key.tenant, "docling_options_json", None) or {}),
        ("api_key", getattr(api_key, "docling_options_json", None) or {}),
        ("request", request_options or {}),
    ]
    for name, payload in layer_sources:
        normalized, layer_warnings = normalize_docling_options(payload)
        if normalized:
            effective = _merge_dicts(effective, normalized)
        layers.append({"name": name, "options": normalized})
        warnings.extend(layer_warnings)

    effective = apply_profile_overrides(effective, profile)
    effective, profile_warnings = normalize_docling_options(effective)
    warnings.extend(profile_warnings)

    profile_definition = get_profile_definition(profile) if profile else None
    if profile_definition:
        for warning in profile_definition.get("warnings") or []:
            warnings.append(str(warning))

    return {
        "profile": profile,
        "profile_definition": profile_definition,
        "layers": layers,
        "effective_options": effective,
        "warnings": list(dict.fromkeys(warnings)),
    }


def build_pdf_pipeline_options(options: dict | None):
    normalized, _warnings = normalize_docling_options(options)
    pipeline_payload = {
        key: value
        for key, value in normalized.items()
        if key in PIPELINE_OPTION_KEYS
    }
    return build_pdf_pipeline_options_from_dict(pipeline_payload)


def option_schema() -> list[dict[str, Any]]:
    return [dict(item) for item in DOC_OPTION_SCHEMA]


def capabilities_payload() -> dict[str, Any]:
    return {
        "input_formats": {
            "implemented": ["pdf"],
            "planned": ["docx", "pptx", "xlsx", "html", "image", "audio"],
            "not_offered": ["remote_services", "external_plugins"],
        },
        "profiles": profile_catalog(),
        "options_schema": option_schema(),
        "exports": {
            "always_generated": list(ALWAYS_GENERATED_EXPORTS),
            "selectable": list(ALLOWED_EXPORTS),
            "notes": {
                "chunks_json": "Compatibility payload containing DocTags, not real chunking yet.",
                "figures_zip": "ZIP download and metadata only; may be empty.",
            },
        },
        "features": {
            "implemented": [
                "pdf_upload",
                "pdf_signature_check",
                "profile_presets",
                "docling_json",
                "markdown",
                "text",
                "doctags",
                "figures_zip",
                "runtime_diagnostics",
            ],
            "planned": ["real_chunking", "vlm_pipeline", "multi_format_upload"],
            "not_offered": ["remote_services", "external_plugins", "asr_audio_video"],
        },
    }
