import base64
import binascii
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from datetime import timedelta
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from celery import chain, shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from clamav_client import clamd
from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
from docling_core.types.doc import DoclingDocument

from .models import (
    Artifact,
    ArtifactKind,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    Document,
    resolve_data_root_path,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
)
from .docling_options import (
    DEFAULT_CHUNKING_OPTIONS,
    DEFAULT_CHUNKS_FORMAT,
    DEFAULT_HUGGINGFACE_CHUNK_TOKENIZER,
    build_pdf_pipeline_options,
    normalize_docling_options,
    validate_docling_options_for_input_format,
    validate_docling_options_payload,
)
from .formats import extension_for_mime_type, format_for_mime_type
from .profiles import build_profile_pipeline_options
from .retention import artifact_expires_at, infected_quarantine_retention_days

DEFAULT_WEBHOOK_EVENTS = ["job.updated"]
DOCLING_UNLIMITED = 9223372036854775807
CHUNK_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)

DocumentConverter = None
PdfFormatOption = None
WordFormatOption = None
PowerpointFormatOption = None
ExcelFormatOption = None
InputFormat = None


class SimpleChunkTokenizer(BaseTokenizer):
    max_tokens: int

    def count_tokens(self, text: str) -> int:
        return len(CHUNK_TOKEN_PATTERN.findall(text or ""))

    def get_max_tokens(self) -> int:
        return self.max_tokens

    def get_tokenizer(self):
        return self.count_tokens


def _load_docling_converter():
    global DocumentConverter, PdfFormatOption, WordFormatOption
    global PowerpointFormatOption, ExcelFormatOption, InputFormat
    if (
        DocumentConverter is None
        or PdfFormatOption is None
        or WordFormatOption is None
        or PowerpointFormatOption is None
        or ExcelFormatOption is None
        or InputFormat is None
    ):
        converter_module = import_module("docling.document_converter")
        base_models_module = import_module("docling.datamodel.base_models")
        if DocumentConverter is None:
            DocumentConverter = converter_module.DocumentConverter
        if PdfFormatOption is None:
            PdfFormatOption = converter_module.PdfFormatOption
        if WordFormatOption is None:
            WordFormatOption = converter_module.WordFormatOption
        if PowerpointFormatOption is None:
            PowerpointFormatOption = converter_module.PowerpointFormatOption
        if ExcelFormatOption is None:
            ExcelFormatOption = converter_module.ExcelFormatOption
        if InputFormat is None:
            InputFormat = base_models_module.InputFormat
    format_option_classes = {
        "pdf": PdfFormatOption,
        "docx": WordFormatOption,
        "pptx": PowerpointFormatOption,
        "xlsx": ExcelFormatOption,
    }
    return DocumentConverter, format_option_classes, InputFormat


def _webhook_max_attempts() -> int:
    return int(getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 5))


def _webhook_initial_backoff_seconds() -> int:
    return int(getattr(settings, "WEBHOOK_INITIAL_BACKOFF_SECONDS", 30))


def _webhook_request_timeout() -> int:
    return int(getattr(settings, "WEBHOOK_REQUEST_TIMEOUT", 10))


def start_ingestion_pipeline(job_id: int):
    result = chain(
        scan_document_task.s(job_id),
        docling_convert_task.s(),
        export_artifacts_task.s(),
        chunk_artifacts_task.s(),
        finalize_job_task.s(),
    ).apply_async(queue=settings.CELERY_DEFAULT_QUEUE)
    try:
        job = IngestionJob.objects.filter(pk=job_id).first()
        if job:
            root = result
            while getattr(root, "parent", None):
                root = root.parent
            job.celery_task_id = root.id
            job.save(update_fields=["celery_task_id"])
    except Exception:
        pass
    return result


def _remove_empty_parent_dirs(start_dir: Path, stop_dir: Path) -> None:
    try:
        data_root = Path(settings.DATA_ROOT).resolve()
        current = start_dir.resolve()
        stop = stop_dir.resolve()
        current.relative_to(stop)
        stop.relative_to(data_root)
    except (OSError, RuntimeError, ValueError):
        return

    while current != stop and current != data_root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _remove_storage_file(relpath: str | None, stop_relpath: str) -> bool:
    abs_path = resolve_data_root_path(relpath)
    if not abs_path:
        return False

    path = Path(abs_path)
    removed = False
    try:
        if path.exists():
            path.unlink()
            removed = True
    except OSError:
        pass

    _remove_empty_parent_dirs(path.parent, Path(settings.DATA_ROOT) / stop_relpath)
    return removed


@shared_task(bind=True)
def cleanup_expired_artifacts(self) -> int:
    now = timezone.now()
    expired_artifacts = Artifact.objects.filter(expires_at__lt=now).exclude(
        job__status__in=(IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING)
    )
    deleted = 0
    for artifact in expired_artifacts:
        _remove_storage_file(artifact.storage_relpath, "artifacts")
        artifact.delete()
        deleted += 1
    return deleted


def _has_active_jobs(doc: Document) -> bool:
    return IngestionJob.objects.filter(
        document=doc,
        status__in=(IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING),
    ).exists()


def _cleanup_artifacts_for_document(doc: Document) -> int:
    artifacts = list(Artifact.objects.filter(job__document=doc))
    for artifact in artifacts:
        _remove_storage_file(artifact.storage_relpath, "artifacts")
    Artifact.objects.filter(pk__in=[artifact.pk for artifact in artifacts]).delete()
    return len(artifacts)


def _cleanup_infected_quarantine_files(now) -> int:
    cleaned = 0
    docs = Document.objects.filter(status=DocumentStatus.INFECTED).select_related("tenant")
    for doc in docs:
        retention_days = infected_quarantine_retention_days(doc.tenant)
        if retention_days <= 0:
            continue
        reference_at = doc.infected_at or doc.modified_at or doc.created_at
        if not reference_at or reference_at > now - timedelta(days=retention_days):
            continue
        relpaths = [doc.storage_relpath_quarantine]
        relpaths.extend(
            IngestionJob.objects.filter(
                document=doc,
                status__in=(
                    IngestionJobStatus.QUARANTINED,
                    IngestionJobStatus.FAILED,
                    IngestionJobStatus.CANCELED,
                    IngestionJobStatus.SUCCEEDED,
                ),
            )
            .exclude(source_relpath__isnull=True)
            .exclude(source_relpath="")
            .values_list("source_relpath", flat=True)
        )
        for relpath in set(relpaths):
            removed = _remove_storage_file(
                relpath,
                os.path.join("uploads", "quarantine"),
            )
            if removed:
                cleaned += 1
    return cleaned


@shared_task(bind=True)
def cleanup_expired_documents(self) -> int:
    now = timezone.now()
    expired_infected_docs = Document.objects.filter(
        expires_at__lt=now,
        status=DocumentStatus.INFECTED,
    )
    cleaned = 0
    for doc in expired_infected_docs:
        if _has_active_jobs(doc):
            continue
        cleaned += _cleanup_artifacts_for_document(doc)
        if doc.storage_relpath_clean:
            _remove_storage_file(doc.storage_relpath_clean, os.path.join("uploads", "clean"))
            doc.storage_relpath_clean = None
            doc.save(update_fields=["storage_relpath_clean"])

    expired_docs = Document.objects.filter(expires_at__lt=now).exclude(
        status=DocumentStatus.INFECTED
    )
    for doc in expired_docs:
        if _has_active_jobs(doc):
            continue
        _cleanup_artifacts_for_document(doc)
        source_relpaths = list(
            IngestionJob.objects.filter(document=doc)
            .exclude(source_relpath__isnull=True)
            .exclude(source_relpath="")
            .values_list("source_relpath", flat=True)
        )
        for relpath in source_relpaths:
            _remove_storage_file(relpath, os.path.join("uploads", "quarantine"))
        _remove_storage_file(
            doc.storage_relpath_quarantine,
            os.path.join("uploads", "quarantine"),
        )
        _remove_storage_file(doc.storage_relpath_clean, os.path.join("uploads", "clean"))
        doc.delete()
        cleaned += 1
    cleaned += _cleanup_infected_quarantine_files(now)
    return cleaned


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _write_artifact(job: IngestionJob, kind: str, relpath: str, data: bytes, content_type: str):
    abs_path = Path(settings.DATA_ROOT) / relpath
    _write_bytes_atomic(abs_path, data)
    checksum = _sha256_bytes(data)
    size_bytes = len(data)
    return Artifact.objects.create(
        tenant=job.tenant,
        created_by_key=job.created_by_key,
        job=job,
        kind=kind,
        storage_relpath=relpath,
        checksum_sha256=checksum,
        size_bytes=size_bytes,
        content_type=content_type,
        expires_at=artifact_expires_at(job.tenant),
    )


def _decode_data_uri(uri: str) -> tuple[bytes, str] | None:
    uri = str(uri)
    if not uri.startswith("data:"):
        return None
    if "," not in uri:
        return None
    header, payload = uri.split(",", 1)
    if ";base64" not in header:
        return None
    mime = header[5:].split(";", 1)[0]
    try:
        raw = base64.b64decode(payload)
    except (ValueError, binascii.Error):
        return None
    extension = mimetypes.guess_extension(mime) or ".bin"
    return raw, extension


def _build_figures_zip(docling_doc: DoclingDocument) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for idx, picture in enumerate(docling_doc.pictures or [], start=1):
            image_ref = getattr(picture, "image", None)
            if not image_ref or not image_ref.uri:
                continue
            decoded = _decode_data_uri(str(image_ref.uri))
            if not decoded:
                continue
            payload, extension = decoded
            archive.writestr(f"figure_{idx}{extension}", payload)
    return buffer.getvalue()


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _artifact_relpath(job: IngestionJob, filename: str) -> str:
    return os.path.join("artifacts", str(job.tenant_id), str(job.id), filename)


def _mark_failed(job: IngestionJob, code: str, message: str, details: dict | None = None):
    prev_status = job.status
    prev_stage = job.stage
    job.status = IngestionJobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.error_details_json = details or {}
    job.finished_at = timezone.now()
    job.recompute_durations()
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)


def _traceback_details(limit: int = 20000) -> dict:
    trace = traceback.format_exc()
    original_len = len(trace)
    truncated = False
    if original_len > limit:
        trace = trace[-limit:]
        truncated = True
    return {
        "traceback": trace,
        "traceback_truncated": truncated,
        "traceback_length": original_len,
    }


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return ""


def _json_safe(value):
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _docling_result_status(result) -> str:
    status = getattr(result, "status", None)
    if status is None:
        return "success"
    value = getattr(status, "value", status)
    return str(value).lower()


def _docling_result_details(result) -> dict:
    return {
        "docling_status": _docling_result_status(result),
        "docling_errors": _json_safe(getattr(result, "errors", []) or []),
    }


def _count_items(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return len(value)
    try:
        return len(value)
    except TypeError:
        return None


def _enum_or_value(value):
    return getattr(value, "value", value)


def _chunking_options(options: dict | None) -> dict:
    normalized, _warnings = normalize_docling_options(options or {})
    chunking = dict(DEFAULT_CHUNKING_OPTIONS)
    configured = normalized.get("chunking")
    if isinstance(configured, dict):
        chunking.update(configured)
    return chunking


def _chunks_format(options: dict | None) -> str:
    normalized, _warnings = normalize_docling_options(options or {})
    return normalized.get("chunks_format") or DEFAULT_CHUNKS_FORMAT


def _build_chunk_tokenizer(tokenizer_config, max_tokens: int):
    if isinstance(tokenizer_config, str):
        tokenizer_name = tokenizer_config.strip()
        if tokenizer_name in ("simple", "regex"):
            return SimpleChunkTokenizer(max_tokens=max_tokens)
        if tokenizer_name in ("default", "huggingface_default"):
            tokenizer_name = DEFAULT_HUGGINGFACE_CHUNK_TOKENIZER
        from docling_core.transforms.chunker.tokenizer.huggingface import (
            HuggingFaceTokenizer,
        )

        return HuggingFaceTokenizer.from_pretrained(
            model_name=tokenizer_name,
            max_tokens=max_tokens,
            local_files_only=True,
        )

    tokenizer_config = dict(tokenizer_config or {})
    kind = tokenizer_config.get("kind", "huggingface")
    if kind == "simple":
        return SimpleChunkTokenizer(max_tokens=max_tokens)

    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )

    model_name = tokenizer_config.get("model_name") or DEFAULT_HUGGINGFACE_CHUNK_TOKENIZER
    kwargs = {
        key: tokenizer_config[key]
        for key in ("local_files_only", "revision")
        if key in tokenizer_config
    }
    kwargs["local_files_only"] = True
    return HuggingFaceTokenizer.from_pretrained(
        model_name=model_name,
        max_tokens=max_tokens,
        **kwargs,
    )


def _build_hybrid_chunker(options: dict | None):
    from docling.chunking import HybridChunker

    chunking = _chunking_options(options)
    max_tokens = chunking["max_tokens"]
    tokenizer = _build_chunk_tokenizer(chunking["tokenizer"], max_tokens)
    return HybridChunker(
        tokenizer=tokenizer,
        repeat_table_header=chunking["repeat_table_header"],
        merge_peers=chunking["merge_peers"],
        omit_header_on_overflow=chunking["omit_header_on_overflow"],
        always_emit_headings=chunking["always_emit_headings"],
    )


def _bbox_payload(bbox) -> dict | None:
    if bbox is None:
        return None
    if hasattr(bbox, "model_dump"):
        try:
            return bbox.model_dump(mode="json")
        except TypeError:
            return bbox.model_dump()
    payload = {
        key: getattr(bbox, key)
        for key in ("l", "t", "r", "b")
        if getattr(bbox, key, None) is not None
    }
    coord_origin = getattr(bbox, "coord_origin", None)
    if coord_origin is not None:
        payload["coord_origin"] = _enum_or_value(coord_origin)
    return payload or None


def _doc_item_payload(item, item_pages: set[int]) -> dict:
    payload = {}
    self_ref = getattr(item, "self_ref", None)
    label = getattr(item, "label", None)
    if self_ref:
        payload["self_ref"] = str(self_ref)
    if label is not None:
        payload["label"] = str(_enum_or_value(label))
    if item_pages:
        payload["pages"] = sorted(item_pages)
    return payload


def _chunk_payload(chunk) -> dict:
    meta = getattr(chunk, "meta", None)
    pages: set[int] = set()
    bounding_boxes = []
    doc_items = []

    for item in getattr(meta, "doc_items", []) or []:
        item_pages: set[int] = set()
        for prov in getattr(item, "prov", []) or []:
            page_no = getattr(prov, "page_no", None)
            if page_no is not None:
                pages.add(page_no)
                item_pages.add(page_no)
            bbox = _bbox_payload(getattr(prov, "bbox", None))
            if bbox:
                box = {"bbox": bbox}
                if page_no is not None:
                    box["page"] = page_no
                self_ref = getattr(item, "self_ref", None)
                if self_ref:
                    box["doc_item"] = str(self_ref)
                charspan = getattr(prov, "charspan", None)
                if charspan is not None:
                    box["charspan"] = list(charspan)
                bounding_boxes.append(box)
        doc_item = _doc_item_payload(item, item_pages)
        if doc_item:
            doc_items.append(doc_item)

    chunk_meta = {
        "pages": sorted(pages),
        "headings": list(getattr(meta, "headings", None) or []),
        "bounding_boxes": bounding_boxes,
        "doc_items": doc_items,
    }
    origin = getattr(meta, "origin", None)
    if origin is not None:
        chunk_meta["origin"] = _json_safe(origin)

    return {
        "text": getattr(chunk, "text", "") or "",
        "meta": chunk_meta,
    }


def _build_hybrid_chunks_payload(docling_doc: DoclingDocument, options: dict | None) -> list[dict]:
    chunker = _build_hybrid_chunker(options)
    return [_chunk_payload(chunk) for chunk in chunker.chunk(dl_doc=docling_doc)]


def _docling_document_metrics(docling_doc: DoclingDocument) -> dict:
    try:
        text = docling_doc.export_to_text()
        text_length = len(text or "")
    except Exception:
        text_length = None
    return {
        "page_count": _count_items(getattr(docling_doc, "pages", None)),
        "table_count": _count_items(getattr(docling_doc, "tables", None)) or 0,
        "picture_count": _count_items(getattr(docling_doc, "pictures", None)) or 0,
        "text_length": text_length,
        "chunks_json_kind": None,
    }


def _docling_limit(value: int | None, fallback: int | None = None) -> int:
    if value is not None:
        return DOCLING_UNLIMITED if value == 0 else value
    if fallback and fallback > 0:
        return fallback
    return DOCLING_UNLIMITED


def _is_canceled(job: IngestionJob) -> bool:
    return job.status == IngestionJobStatus.CANCELED


def _clamav_client():
    socket_path = getattr(settings, "CLAMAV_SOCKET", "")
    if socket_path:
        return clamd.ClamdUnixSocket(socket_path)
    return clamd.ClamdNetworkSocket(settings.CLAMAV_HOST, settings.CLAMAV_PORT)


@shared_task(bind=True)
def scan_pdf_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.SCANNING
    if not job.started_at:
        job.started_at = timezone.now()
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

    start = time.monotonic()
    document = job.document
    relpath = job.source_relpath or document.storage_relpath_quarantine
    abs_path = resolve_data_root_path(relpath)
    if not abs_path or not os.path.exists(abs_path):
        _mark_failed(job, "MISSING_SOURCE_FILE", "Source file is missing for scan.")
        raise RuntimeError("Missing source file")

    try:
        scanner = _clamav_client()
        results = scanner.scan(abs_path)
    except Exception as exc:
        _mark_failed(job, "CLAMAV_UNAVAILABLE", str(exc))
        raise
    if not isinstance(results, dict):
        _mark_failed(job, "CLAMAV_INVALID_RESPONSE", "Invalid scan response from ClamAV")
        raise RuntimeError("ClamAV invalid response")

    status, reason = results.get(abs_path, ("ERROR", "No scan result"))
    if status == "FOUND":
        _remove_storage_file(document.storage_relpath_clean, os.path.join("uploads", "clean"))
        document.status = DocumentStatus.INFECTED
        document.infected_at = timezone.now()
        if job.source_relpath:
            document.storage_relpath_quarantine = job.source_relpath
        document.storage_relpath_clean = None
        document.save()
        prev_status = job.status
        prev_stage = job.stage
        job.status = IngestionJobStatus.QUARANTINED
        job.error_code = "VIRUS_FOUND"
        job.error_message = reason or "Virus detected"
        job.finished_at = timezone.now()
        job.recompute_durations()
        job.save()
        queue_job_webhooks(job, prev_status, prev_stage)
        raise RuntimeError("Virus found")
    if status == "ERROR":
        _mark_failed(job, "VIRUS_SCAN_ERROR", reason or "Scan error")
        raise RuntimeError("Virus scan error")

    clean_relpath = os.path.join(
        "uploads",
        "clean",
        str(job.tenant_id),
        f"{document.uuid}{extension_for_mime_type(document.mime_type)}",
    )
    clean_abspath = resolve_data_root_path(clean_relpath)
    os.makedirs(os.path.dirname(clean_abspath), exist_ok=True)
    os.replace(abs_path, clean_abspath)
    _remove_storage_file(
        document.storage_relpath_quarantine,
        os.path.join("uploads", "quarantine"),
    )

    document.status = DocumentStatus.CLEAN
    document.storage_relpath_clean = clean_relpath
    document.infected_at = None
    document.save()

    job.scan_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


scan_document_task = scan_pdf_task


@shared_task(bind=True)
def docling_convert_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.CONVERTING
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

    start = time.monotonic()
    document = job.document

    max_pages_option = job.options_json.get("max_num_pages") if job.options_json else None
    max_pages = _docling_limit(max_pages_option, settings.MAX_PAGES)

    max_file_size_option = job.options_json.get("max_file_size") if job.options_json else None
    max_file_size = _docling_limit(
        max_file_size_option,
        settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024,
    )

    try:
        validate_docling_options_payload(job.options_json or {})
        document_format = format_for_mime_type(document.mime_type)
        if not document_format:
            raise ValidationError("Document input format is not supported.")
        validate_docling_options_for_input_format(
            job.options_json or {}, document_format.key, job.profile
        )
    except ValidationError as exc:
        _mark_failed(
            job,
            "INVALID_OPTIONS",
            _validation_message(exc),
            {"options_json": job.options_json or {}},
        )
        return job_id

    try:
        document_converter, format_option_classes, input_format = _load_docling_converter()
        docling_format = getattr(input_format, document_format.docling_input_format)
        format_options = {}
        if document_format.key == "pdf":
            pipeline_options = build_pdf_pipeline_options(job.options_json or {})
            if not pipeline_options:
                pipeline_options = build_profile_pipeline_options(job.profile)
            if pipeline_options:
                format_options[docling_format] = format_option_classes["pdf"](
                    pipeline_options=pipeline_options
                )
        else:
            format_options[docling_format] = format_option_classes[document_format.key]()
        converter = document_converter(
            allowed_formats=[docling_format],
            format_options=format_options or None,
        )
        result = converter.convert(
            document.get_clean_path(),
            max_num_pages=max_pages,
            max_file_size=max_file_size,
        )
    except Exception as exc:
        _mark_failed(job, "DOCLING_CONVERT_FAILED", str(exc), _traceback_details())
        raise

    status = _docling_result_status(result)
    if status != "success":
        code = (
            "DOCLING_PARTIAL_SUCCESS"
            if status == "partial_success"
            else "DOCLING_CONVERT_FAILED"
        )
        message = f"Docling conversion did not complete successfully: {status}"
        _mark_failed(job, code, message, _docling_result_details(result))
        raise RuntimeError(message)

    try:
        docling_doc = result.document
        relpath = _artifact_relpath(job, "docling.json")
        payload = json.dumps(
            docling_doc.export_to_dict(),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        _write_artifact(
            job,
            ArtifactKind.DOCLING_JSON,
            relpath,
            payload,
            "application/json",
        )
    except Exception as exc:
        _mark_failed(job, "DOCLING_CONVERT_FAILED", str(exc), _traceback_details())
        raise

    job.docling_version = _package_version("docling")
    job.docling_core_version = _package_version("docling-core")
    job.docling_parse_version = _package_version("docling-parse")
    job.runtime_json = {
        "DOCLING_DEVICE": os.environ.get("DOCLING_DEVICE", ""),
        "DOCLING_NUM_THREADS": os.environ.get("DOCLING_NUM_THREADS", ""),
        "HF_HOME": os.environ.get("HF_HOME", ""),
    }
    job.convert_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


@shared_task(bind=True)
def export_artifacts_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.EXPORTING
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

    start = time.monotonic()
    relpath = _artifact_relpath(job, "docling.json")
    abs_path = Path(settings.DATA_ROOT) / relpath

    try:
        with open(abs_path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        docling_doc = DoclingDocument.model_validate(data)
    except Exception as exc:
        _mark_failed(job, "DOCLING_LOAD_FAILED", str(exc), _traceback_details())
        raise

    metrics = _docling_document_metrics(docling_doc)
    job.result_metrics_json = metrics
    page_count = metrics.get("page_count")
    if page_count is not None:
        job.document.page_count = page_count
        job.document.save(update_fields=["page_count", "modified_at"])

    exports = job.options_json.get("exports") if job.options_json else None
    if exports is None:
        exports = ["markdown", "text", "doctags"]

    try:
        if "markdown" in exports:
            markdown = docling_doc.export_to_markdown()
            _write_artifact(
                job,
                ArtifactKind.MARKDOWN,
                _artifact_relpath(job, "document.md"),
                markdown.encode("utf-8"),
                "text/markdown",
            )
        if "text" in exports:
            text = docling_doc.export_to_text()
            _write_artifact(
                job,
                ArtifactKind.TEXT,
                _artifact_relpath(job, "document.txt"),
                text.encode("utf-8"),
                "text/plain",
            )
        if "doctags" in exports:
            doctags = docling_doc.export_to_doctags()
            _write_artifact(
                job,
                ArtifactKind.DOCTAGS,
                _artifact_relpath(job, "document.doctags"),
                doctags.encode("utf-8"),
                "text/plain",
            )
        if "figures_zip" in exports:
            payload = _build_figures_zip(docling_doc)
            _write_artifact(
                job,
                ArtifactKind.FIGURES_ZIP,
                _artifact_relpath(job, "figures.zip"),
                payload,
                "application/zip",
            )
    except Exception as exc:
        _mark_failed(job, "DOCLING_EXPORT_FAILED", str(exc), _traceback_details())
        raise

    job.export_ms = int((time.monotonic() - start) * 1000)
    job.save()
    return job_id


@shared_task(bind=True)
def chunk_artifacts_task(self, job_id: int) -> int:
    job = IngestionJob.objects.select_related("document").get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.CHUNKING
    job.status = IngestionJobStatus.RUNNING
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)

    start = time.monotonic()
    try:
        validate_docling_options_payload(job.options_json or {})
    except ValidationError as exc:
        job.chunk_ms = int((time.monotonic() - start) * 1000)
        job.save(update_fields=["chunk_ms"])
        message = _validation_message(exc)
        _mark_failed(
            job,
            "INVALID_OPTIONS",
            message,
            {"options_json": job.options_json or {}},
        )
        raise RuntimeError(message)

    exports = job.options_json.get("exports") if job.options_json else None
    if exports is None:
        exports = ["markdown", "text", "doctags"]
    if "chunks_json" not in exports:
        job.chunk_ms = int((time.monotonic() - start) * 1000)
        job.save(update_fields=["chunk_ms"])
        return job_id

    relpath = _artifact_relpath(job, "docling.json")
    abs_path = Path(settings.DATA_ROOT) / relpath

    try:
        with open(abs_path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        docling_doc = DoclingDocument.model_validate(data)
    except Exception as exc:
        job.chunk_ms = int((time.monotonic() - start) * 1000)
        job.save(update_fields=["chunk_ms"])
        _mark_failed(job, "DOCLING_LOAD_FAILED", str(exc), _traceback_details())
        raise

    chunks_format = _chunks_format(job.options_json or {})
    try:
        if chunks_format == "doctags_compat":
            doctags = docling_doc.export_to_doctags()
            payload_data = {"format": "doctags", "content": doctags}
            chunk_count = None
            chunks_json_kind = "doctags_compatibility_payload"
        else:
            payload_data = _build_hybrid_chunks_payload(docling_doc, job.options_json or {})
            chunk_count = len(payload_data)
            chunks_json_kind = "hybrid_chunker"
        payload = json.dumps(
            payload_data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        _write_artifact(
            job,
            ArtifactKind.CHUNKS_JSON,
            _artifact_relpath(job, "chunks.json"),
            payload,
            "application/json",
        )
    except Exception as exc:
        job.chunk_ms = int((time.monotonic() - start) * 1000)
        job.save(update_fields=["chunk_ms"])
        _mark_failed(job, "DOCLING_CHUNK_FAILED", str(exc), _traceback_details())
        raise

    metrics = dict(job.result_metrics_json or {})
    metrics["chunks_json_kind"] = chunks_json_kind
    if chunk_count is not None:
        metrics["chunk_count"] = chunk_count
    job.result_metrics_json = metrics
    job.chunk_ms = int((time.monotonic() - start) * 1000)
    job.save(update_fields=["result_metrics_json", "chunk_ms"])
    return job_id


@shared_task(bind=True)
def finalize_job_task(self, job_id: int) -> int:
    job = IngestionJob.objects.get(pk=job_id)
    if _is_canceled(job):
        return job_id
    prev_status = job.status
    prev_stage = job.stage
    job.stage = IngestionStage.FINALIZING
    job.status = IngestionJobStatus.SUCCEEDED
    job.finished_at = timezone.now()
    job.recompute_durations()
    if self.request.id:
        job.celery_task_id = self.request.id
    job.save()
    queue_job_webhooks(job, prev_status, prev_stage)
    return job_id


def _isoformat(value):
    if not value:
        return None
    return value.isoformat()


def _job_webhook_payload(job: IngestionJob, prev_status: str | None, prev_stage: str | None) -> dict:
    payload = {
        "event": "job.updated",
        "job_id": job.id,
        "job_uuid": str(job.uuid),
        "document_id": job.document_id,
        "external_uuid": str(job.external_uuid) if job.external_uuid else None,
        "profile": job.profile or None,
        "comparison_id": str(job.comparison_id) if job.comparison_id else None,
        "status": job.status,
        "stage": job.stage,
        "previous_status": prev_status,
        "previous_stage": prev_stage,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "queued_at": _isoformat(job.queued_at),
        "started_at": _isoformat(job.started_at),
        "finished_at": _isoformat(job.finished_at),
        "created_at": _isoformat(job.created_at),
        "modified_at": _isoformat(job.modified_at),
    }
    if getattr(settings, "WEBHOOK_INCLUDE_ERROR_DETAILS", False):
        payload["error_details"] = job.error_details_json
    return payload


def queue_job_webhooks(job: IngestionJob, prev_status: str | None, prev_stage: str | None) -> int:
    if prev_status == job.status and prev_stage == job.stage:
        return 0
    endpoints = WebhookEndpoint.objects.filter(tenant=job.tenant, enabled=True)
    if not endpoints.exists():
        return 0
    payload = _job_webhook_payload(job, prev_status, prev_stage)
    delivery_ids = []
    for endpoint in endpoints:
        events = endpoint.events or DEFAULT_WEBHOOK_EVENTS
        if "job.updated" not in events:
            continue
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="job.updated",
            payload_json=payload,
            status=WebhookDeliveryStatus.PENDING,
        )
        delivery_ids.append(delivery.id)
    for delivery_id in delivery_ids:
        deliver_webhook_delivery.delay(delivery_id)
    return len(delivery_ids)


@shared_task(bind=True)
def deliver_webhook_delivery(self, delivery_id: int) -> bool:
    delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
    if delivery.status == WebhookDeliveryStatus.DELIVERED:
        return True
    if delivery.status == WebhookDeliveryStatus.FAILED:
        return False

    endpoint = delivery.endpoint
    if not endpoint.enabled:
        delivery.status = WebhookDeliveryStatus.FAILED
        delivery.last_error = "Endpoint disabled"
        delivery.save(update_fields=["status", "last_error", "modified_at"])
        return False

    payload = delivery.payload_json or {}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "DocumentRefinery-Webhooks/1.0",
        "X-DocRefinery-Event": delivery.event_type,
        "X-DocRefinery-Delivery": str(delivery.uuid),
    }
    if endpoint.secret:
        signature = hmac.new(endpoint.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-DocRefinery-Signature"] = f"sha256={signature}"

    request = urllib.request.Request(endpoint.url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_webhook_request_timeout()) as response:
            code = response.getcode()
        if 200 <= code < 300:
            now = timezone.now()
            delivery.status = WebhookDeliveryStatus.DELIVERED
            delivery.response_code = code
            delivery.delivered_at = now
            delivery.next_retry_at = None
            delivery.save(
                update_fields=[
                    "status",
                    "response_code",
                    "delivered_at",
                    "next_retry_at",
                    "modified_at",
                ]
            )
            WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
                last_success_at=now,
                modified_at=now,
            )
            return True
        raise urllib.error.HTTPError(
            endpoint.url, code, f"Unexpected status {code}", hdrs=None, fp=None
        )
    except Exception as exc:
        delivery.attempt += 1
        response_code = getattr(exc, "code", None)
        delivery.response_code = response_code
        delivery.last_error = str(exc)
        if delivery.attempt >= _webhook_max_attempts():
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.next_retry_at = None
        else:
            delivery.status = WebhookDeliveryStatus.RETRYING
            delay = _webhook_initial_backoff_seconds() * (2 ** (delivery.attempt - 1))
            delivery.next_retry_at = timezone.now() + timedelta(seconds=delay)
        delivery.save(
            update_fields=[
                "status",
                "attempt",
                "response_code",
                "last_error",
                "next_retry_at",
                "modified_at",
            ]
        )
        now = timezone.now()
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
            last_failure_at=now,
            modified_at=now,
        )
        if delivery.status == WebhookDeliveryStatus.RETRYING:
            if not getattr(self.request, "called_directly", False) and not getattr(
                self.request, "is_eager", False
            ):
                self.apply_async(args=[delivery.id], countdown=delay)
        return False
