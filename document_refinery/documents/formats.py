from __future__ import annotations

import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentFormat:
    key: str
    label: str
    primary_mime_type: str
    mime_types: tuple[str, ...]
    extensions: tuple[str, ...]
    docling_input_format: str
    invalid_error_code: str
    invalid_message: str
    required_zip_members: tuple[str, ...] = ()

    @property
    def primary_extension(self) -> str:
        return self.extensions[0]


PDF = DocumentFormat(
    key="pdf",
    label="PDF",
    primary_mime_type="application/pdf",
    mime_types=("application/pdf", "application/x-pdf"),
    extensions=(".pdf",),
    docling_input_format="PDF",
    invalid_error_code="INVALID_PDF",
    invalid_message="File does not look like a PDF.",
)
DOCX = DocumentFormat(
    key="docx",
    label="Word document",
    primary_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    mime_types=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    extensions=(".docx",),
    docling_input_format="DOCX",
    invalid_error_code="INVALID_DOCUMENT",
    invalid_message="File does not look like a DOCX document.",
    required_zip_members=("[Content_Types].xml", "_rels/.rels", "word/document.xml"),
)
PPTX = DocumentFormat(
    key="pptx",
    label="PowerPoint presentation",
    primary_mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    mime_types=(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    extensions=(".pptx",),
    docling_input_format="PPTX",
    invalid_error_code="INVALID_DOCUMENT",
    invalid_message="File does not look like a PPTX presentation.",
    required_zip_members=("[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"),
)
XLSX = DocumentFormat(
    key="xlsx",
    label="Excel workbook",
    primary_mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    mime_types=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    extensions=(".xlsx",),
    docling_input_format="XLSX",
    invalid_error_code="INVALID_DOCUMENT",
    invalid_message="File does not look like an XLSX workbook.",
    required_zip_members=("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"),
)

SUPPORTED_DOCUMENT_FORMATS = (PDF, DOCX, PPTX, XLSX)
SUPPORTED_UPLOAD_MIME_TYPES = tuple(
    mime_type
    for document_format in SUPPORTED_DOCUMENT_FORMATS
    for mime_type in document_format.mime_types
)
IMPLEMENTED_INPUT_FORMATS = tuple(
    document_format.key for document_format in SUPPORTED_DOCUMENT_FORMATS
)
PLANNED_INPUT_FORMATS = ("html", "image", "audio")

_FORMAT_BY_MIME_TYPE = {
    mime_type: document_format
    for document_format in SUPPORTED_DOCUMENT_FORMATS
    for mime_type in document_format.mime_types
}
_FORMAT_BY_EXTENSION = {
    extension: document_format
    for document_format in SUPPORTED_DOCUMENT_FORMATS
    for extension in document_format.extensions
}


def normalize_mime_type(value: str | None) -> str:
    return (value or "").strip().lower()


def format_for_mime_type(value: str | None) -> DocumentFormat | None:
    return _FORMAT_BY_MIME_TYPE.get(normalize_mime_type(value))


def format_for_extension(value: str | None) -> DocumentFormat | None:
    if not value:
        return None
    extension = value.strip().lower()
    if extension and not extension.startswith("."):
        extension = "." + extension
    return _FORMAT_BY_EXTENSION.get(extension)


def extension_for_mime_type(value: str | None) -> str:
    document_format = format_for_mime_type(value)
    return document_format.primary_extension if document_format else ".bin"


def _remember_position(uploaded):
    try:
        return uploaded.tell()
    except Exception:
        return None


def _restore_position(uploaded, position) -> None:
    try:
        uploaded.seek(0 if position is None else position)
    except Exception:
        pass


def validate_uploaded_file_signature(uploaded, document_format: DocumentFormat) -> tuple[str, str] | None:
    position = _remember_position(uploaded)
    try:
        if document_format.key == PDF.key:
            try:
                return None if uploaded.read(5) == b"%PDF-" else (
                    document_format.invalid_error_code,
                    document_format.invalid_message,
                )
            except Exception:
                return document_format.invalid_error_code, document_format.invalid_message

        if document_format.required_zip_members:
            try:
                with zipfile.ZipFile(uploaded, "r") as archive:
                    names = set(archive.namelist())
            except (OSError, zipfile.BadZipFile):
                return document_format.invalid_error_code, document_format.invalid_message
            missing = [
                member for member in document_format.required_zip_members if member not in names
            ]
            if missing:
                return document_format.invalid_error_code, document_format.invalid_message
            return None
    finally:
        _restore_position(uploaded, position)
    return None
