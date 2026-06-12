from __future__ import annotations

from collections.abc import Mapping

from rest_framework import status
from rest_framework.exceptions import ErrorDetail
from rest_framework.pagination import PageNumberPagination
from rest_framework.renderers import JSONRenderer
from rest_framework.views import exception_handler as drf_exception_handler


class StandardPageNumberPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    response.data = normalize_error_data(
        response.data,
        response.status_code,
        context.get("request"),
    )
    return response


class StandardJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}
        response = renderer_context.get("response")
        request = renderer_context.get("request")
        if response is not None and response.status_code >= 400:
            data = normalize_error_data(data, response.status_code, request)
            response.data = data
        return super().render(data, accepted_media_type, renderer_context)


def normalize_error_data(data, status_code: int, request=None) -> dict[str, object]:
    request_id = _request_id(request)
    if isinstance(data, Mapping) and data.get("error_code") and data.get("message"):
        return {**data, "request_id": data.get("request_id") or request_id}

    message = _message_for_data(data) or _default_message_for_status(status_code)
    error_code = _error_code_for_status(status_code, message)
    return {
        "error_code": error_code,
        "message": message,
        "request_id": request_id,
    }


def _request_id(request) -> str:
    if request is None:
        return ""
    django_request = getattr(request, "_request", request)
    return getattr(django_request, "request_id", "") or ""


def _message_for_data(data) -> str:
    if data is None:
        return ""
    if isinstance(data, Mapping):
        detail = data.get("detail")
        if detail:
            return _stringify_detail(detail)
        return _stringify_detail(data)
    return _stringify_detail(data)


def _stringify_detail(detail) -> str:
    if isinstance(detail, ErrorDetail):
        return str(detail)
    if isinstance(detail, str):
        return detail
    if isinstance(detail, Mapping):
        parts = []
        for key, value in detail.items():
            if key in {"error_code", "message", "request_id"}:
                continue
            rendered = _stringify_detail(value)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "; ".join(parts)
    if isinstance(detail, (list, tuple)):
        return "; ".join(filter(None, (_stringify_detail(item) for item in detail)))
    return str(detail)


def _error_code_for_status(status_code: int, message: str = "") -> str:
    if status_code == status.HTTP_403_FORBIDDEN and "scope" in message.lower():
        return "INSUFFICIENT_SCOPE"
    return {
        status.HTTP_400_BAD_REQUEST: "VALIDATION_ERROR",
        status.HTTP_401_UNAUTHORIZED: "AUTHENTICATION_REQUIRED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_406_NOT_ACCEPTABLE: "NOT_ACCEPTABLE",
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "SERVER_ERROR",
        status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
    }.get(status_code, "ERROR")


def _default_message_for_status(status_code: int) -> str:
    return {
        status.HTTP_400_BAD_REQUEST: "Request is invalid.",
        status.HTTP_401_UNAUTHORIZED: "Authentication credentials were not provided.",
        status.HTTP_403_FORBIDDEN: "Permission denied.",
        status.HTTP_404_NOT_FOUND: "Not found.",
        status.HTTP_405_METHOD_NOT_ALLOWED: "Method not allowed.",
        status.HTTP_406_NOT_ACCEPTABLE: "Requested representation is not available.",
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "Unsupported media type.",
        status.HTTP_429_TOO_MANY_REQUESTS: "Request was throttled.",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "Internal server error.",
        status.HTTP_503_SERVICE_UNAVAILABLE: "Service unavailable.",
    }.get(status_code, "Request failed.")
