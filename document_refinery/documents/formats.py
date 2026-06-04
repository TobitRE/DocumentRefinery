from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass

OOXML_MAX_ZIP_ENTRIES = 10_000
OOXML_MAX_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
OOXML_MAX_ENTRY_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
OOXML_MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
OOXML_MAX_COMPRESSION_RATIO = 200
OOXML_ZIP_SAFETY_MESSAGE = "Office document ZIP metadata exceeds safety limits."
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_EOCD_MIN_SIZE = 22
_ZIP_EOCD_MAX_COMMENT_SIZE = 65_535
_ZIP64_SENTINEL_SHORT = 0xFFFF
_ZIP64_SENTINEL_LONG = 0xFFFFFFFF


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


def _file_size(uploaded) -> int | None:
    position = _remember_position(uploaded)
    try:
        uploaded.seek(0, 2)
        return uploaded.tell()
    except Exception:
        return None
    finally:
        _restore_position(uploaded, position)


def _read_zip_tail(uploaded, file_size: int) -> bytes | None:
    position = _remember_position(uploaded)
    try:
        read_size = min(
            file_size,
            _ZIP_EOCD_MIN_SIZE + _ZIP_EOCD_MAX_COMMENT_SIZE,
        )
        uploaded.seek(file_size - read_size)
        data = uploaded.read(read_size)
        return data if isinstance(data, bytes) else None
    except Exception:
        return None
    finally:
        _restore_position(uploaded, position)


def _ooxml_zip_preflight_error(uploaded, invalid_message: str) -> str | None:
    file_size = _file_size(uploaded)
    if file_size is None or file_size < _ZIP_EOCD_MIN_SIZE:
        return invalid_message

    tail = _read_zip_tail(uploaded, file_size)
    if tail is None:
        return invalid_message

    eocd_index = tail.rfind(_ZIP_EOCD_SIGNATURE)
    if eocd_index < 0 or len(tail) - eocd_index < _ZIP_EOCD_MIN_SIZE:
        return invalid_message

    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_this_disk,
        entries_total,
        central_directory_size,
        central_directory_offset,
        _comment_size,
    ) = struct.unpack_from("<4s4H2LH", tail, eocd_index)

    if (
        disk_number
        or central_directory_disk
        or entries_this_disk != entries_total
        or central_directory_offset + central_directory_size > file_size
    ):
        return invalid_message

    if (
        entries_total == _ZIP64_SENTINEL_SHORT
        or central_directory_size == _ZIP64_SENTINEL_LONG
        or central_directory_offset == _ZIP64_SENTINEL_LONG
        or entries_total > OOXML_MAX_ZIP_ENTRIES
        or central_directory_size > OOXML_MAX_CENTRAL_DIRECTORY_BYTES
    ):
        return OOXML_ZIP_SAFETY_MESSAGE

    return None


def _ooxml_zip_metadata_error(infos: list[zipfile.ZipInfo]) -> str | None:
    if len(infos) > OOXML_MAX_ZIP_ENTRIES:
        return OOXML_ZIP_SAFETY_MESSAGE

    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        uncompressed = max(int(info.file_size or 0), 0)
        compressed = max(int(info.compress_size or 0), 0)
        if uncompressed > OOXML_MAX_ENTRY_UNCOMPRESSED_BYTES:
            return OOXML_ZIP_SAFETY_MESSAGE
        total_uncompressed += uncompressed
        total_compressed += compressed

    if total_uncompressed > OOXML_MAX_TOTAL_UNCOMPRESSED_BYTES:
        return OOXML_ZIP_SAFETY_MESSAGE
    if total_uncompressed and not total_compressed:
        return OOXML_ZIP_SAFETY_MESSAGE
    if (
        total_compressed
        and total_uncompressed / total_compressed > OOXML_MAX_COMPRESSION_RATIO
    ):
        return OOXML_ZIP_SAFETY_MESSAGE

    return None


def validate_uploaded_file_signature(
    uploaded, document_format: DocumentFormat
) -> tuple[str, str] | None:
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
            preflight_error = _ooxml_zip_preflight_error(
                uploaded,
                document_format.invalid_message,
            )
            if preflight_error:
                return document_format.invalid_error_code, preflight_error
            try:
                with zipfile.ZipFile(uploaded, "r") as archive:
                    infos = archive.infolist()
                    metadata_error = _ooxml_zip_metadata_error(infos)
                    if metadata_error:
                        return document_format.invalid_error_code, metadata_error
                    names = {info.filename for info in infos}
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
