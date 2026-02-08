from django.core.exceptions import ValidationError

DEFAULT_ALLOWED_UPLOAD_MIME_TYPES = (
    "application/pdf",
    "application/x-pdf",
)


def validate_docling_options(options: dict | None) -> None:
    if options in (None, {}):
        return
    if not isinstance(options, dict):
        raise ValidationError("Docling options must be a JSON object.")

    for key, value in options.items():
        if key in ("max_num_pages", "max_file_size"):
            if not isinstance(value, int) or value < 0:
                raise ValidationError(f"{key} must be a non-negative integer.")
        if key == "exports":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValidationError("exports must be a list of strings.")
        if key == "ocr":
            if not isinstance(value, bool):
                raise ValidationError("ocr must be a boolean.")
        if key == "ocr_languages":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValidationError("ocr_languages must be a list of strings.")


def _normalize_mime_type(value: str) -> str:
    return value.strip().lower()


def validate_allowed_upload_mime_types(mime_types: list[str] | None) -> list[str]:
    if mime_types is None:
        return list(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)
    if not isinstance(mime_types, list):
        raise ValidationError("Allowed upload MIME types must be a list of strings.")
    if not all(isinstance(item, str) for item in mime_types):
        raise ValidationError("Allowed upload MIME types must be a list of strings.")

    normalized = []
    for item in mime_types:
        value = _normalize_mime_type(item)
        if value:
            normalized.append(value)
    normalized = list(dict.fromkeys(normalized))

    if not normalized:
        raise ValidationError("At least one allowed upload MIME type is required.")

    allowed = set(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)
    unsupported = [item for item in normalized if item not in allowed]
    if unsupported:
        raise ValidationError(
            "Unsupported upload MIME types: "
            + ", ".join(unsupported)
            + ". Supported values are: "
            + ", ".join(DEFAULT_ALLOWED_UPLOAD_MIME_TYPES)
            + "."
        )
    return normalized
