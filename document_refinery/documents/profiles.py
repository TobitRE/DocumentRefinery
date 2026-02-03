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
            "ocr_options": {"lang": ["auto"], "force_full_page_ocr": True},
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
            "do_picture_description": True,
            "do_picture_classification": True,
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
    if not pipeline_options:
        return None
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    return PdfPipelineOptions.model_validate(pipeline_options)
