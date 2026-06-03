from __future__ import annotations

from copy import deepcopy


PROFILE_DEFINITIONS: dict[str, dict] = {
    "fast_text": {
        "label": "Fast text",
        "description": "Born-digital PDF extraction with OCR and table detection disabled.",
        "resource_level": "low",
        "feature_status": "implemented",
        "warnings": [],
        "pipeline_options": {
            "do_ocr": False,
            "do_table_structure": False,
        },
        "exports": ["text", "markdown", "doctags"],
    },
    "ocr_only": {
        "label": "OCR only",
        "description": "OCR-focused PDF extraction for scanned pages without table enrichment.",
        "resource_level": "medium",
        "feature_status": "implemented",
        "warnings": [],
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": False,
            "ocr_options": {"kind": "rapidocr", "lang": [], "force_full_page_ocr": True},
        },
        "exports": ["text", "markdown", "doctags"],
    },
    "structured": {
        "label": "Structured",
        "description": "OCR plus table structure and parsed-page output for layout-sensitive PDFs.",
        "resource_level": "high",
        "feature_status": "implemented",
        "warnings": [],
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": True,
            "generate_parsed_pages": True,
            "ocr_options": {"kind": "rapidocr", "lang": []},
        },
        "exports": ["text", "markdown", "doctags", "chunks_json"],
    },
    "full_vlm": {
        "label": "Full legacy image export",
        "description": (
            "Compatibility profile for OCR, table structure, parsed content, and figure image "
            "export. It does not enable a real VLM pipeline."
        ),
        "resource_level": "high",
        "feature_status": "implemented",
        "warnings": [
            "Legacy profile name: full_vlm does not currently enable VLM inference.",
        ],
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": True,
            "generate_picture_images": True,
            "images_scale": 2.0,
            "ocr_options": {"kind": "rapidocr", "lang": []},
        },
        "exports": ["text", "markdown", "doctags", "chunks_json", "figures_zip"],
    },
}

PROFILE_NAMES = tuple(PROFILE_DEFINITIONS.keys())


def get_profile_definition(profile: str | None) -> dict | None:
    if not profile:
        return None
    definition = PROFILE_DEFINITIONS.get(profile)
    return deepcopy(definition) if definition else None


def apply_profile_to_options(options: dict | None, profile: str | None) -> dict | None:
    if not profile:
        return options
    definition = get_profile_definition(profile)
    if not definition:
        return options
    merged = dict(options or {})
    exports = definition.get("exports")
    if exports:
        merged["exports"] = list(exports)
    return merged


def build_pdf_pipeline_options_from_dict(pipeline_options: dict | None):
    pipeline_options = dict(pipeline_options or {})
    if isinstance(pipeline_options, dict) and "ocr_options" in pipeline_options:
        ocr_options = pipeline_options.get("ocr_options")
        if isinstance(ocr_options, dict):
            normalized = dict(ocr_options)
            if "kind" not in normalized:
                normalized["kind"] = "auto"
            kind = normalized.get("kind")
            from docling.datamodel import pipeline_options as docling_opts

            kind_map = {
                "auto": docling_opts.OcrAutoOptions,
                "rapidocr": docling_opts.RapidOcrOptions,
                "easyocr": docling_opts.EasyOcrOptions,
                "tesseract": docling_opts.TesseractOcrOptions,
                "tesseract_cli": docling_opts.TesseractCliOcrOptions,
                "mac": docling_opts.OcrMacOptions,
            }
            cls = kind_map.get(kind, docling_opts.OcrAutoOptions)
            if cls is docling_opts.OcrAutoOptions and "lang" not in normalized:
                normalized["lang"] = []
            allowed = set(getattr(cls, "model_fields", {}).keys())
            payload = {key: value for key, value in normalized.items() if key in allowed}
            pipeline_options = {**pipeline_options, "ocr_options": cls(**payload)}
    if not pipeline_options:
        return None
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    return PdfPipelineOptions.model_validate(pipeline_options)


def build_profile_pipeline_options(profile: str | None):
    if not profile:
        return None
    definition = get_profile_definition(profile)
    if not definition:
        return None
    return build_pdf_pipeline_options_from_dict(definition.get("pipeline_options") or {})


def profile_catalog() -> list[dict]:
    catalog = []
    for name in PROFILE_NAMES:
        definition = get_profile_definition(name) or {}
        pipeline_options = definition.get("pipeline_options") or {}
        catalog.append(
            {
                "name": name,
                "label": definition.get("label") or name,
                "description": definition.get("description") or "",
                "resource_level": definition.get("resource_level") or "unknown",
                "feature_status": definition.get("feature_status") or "implemented",
                "pipeline_options": pipeline_options,
                "exports": list(definition.get("exports") or []),
                "warnings": list(definition.get("warnings") or []),
                "capabilities": {
                    "ocr": bool(pipeline_options.get("do_ocr")),
                    "table_structure": bool(pipeline_options.get("do_table_structure")),
                    "parsed_pages": bool(pipeline_options.get("generate_parsed_pages")),
                    "picture_images": bool(pipeline_options.get("generate_picture_images")),
                    "vlm_pipeline": False,
                    "real_chunking": False,
                },
            }
        )
    return catalog
