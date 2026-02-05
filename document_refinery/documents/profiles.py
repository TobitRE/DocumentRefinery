from __future__ import annotations

from copy import deepcopy


PROFILE_DEFINITIONS: dict[str, dict] = {
    "fast_text": {
        "pipeline_options": {
            "do_ocr": False,
            "do_table_structure": False,
            "do_picture_description": False,
            "do_picture_classification": False,
        },
        "exports": ["text", "markdown", "doctags"],
    },
    "ocr_only": {
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": False,
            "do_picture_description": False,
            "do_picture_classification": False,
            "ocr_options": {"kind": "auto", "lang": [], "force_full_page_ocr": True},
        },
        "exports": ["text", "markdown", "doctags"],
    },
    "structured": {
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": True,
            "generate_parsed_pages": True,
        },
        "exports": ["text", "markdown", "doctags", "chunks_json"],
    },
    "full_vlm": {
        "pipeline_options": {
            "do_ocr": True,
            "do_table_structure": True,
            "do_picture_description": False,
            "do_picture_classification": False,
            "generate_picture_images": True,
            "images_scale": 2.0,
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


def build_profile_pipeline_options(profile: str | None):
    if not profile:
        return None
    definition = get_profile_definition(profile)
    if not definition:
        return None
    pipeline_options = definition.get("pipeline_options") or {}
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
