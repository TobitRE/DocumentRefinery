from django.core.exceptions import ValidationError


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
