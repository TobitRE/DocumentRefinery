import uuid

from django.utils.deprecation import MiddlewareMixin

from .logging import reset_request_id, set_request_id


class RequestIDMiddleware(MiddlewareMixin):
    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def process_request(self, request):
        request_id = request.META.get(self.header_name) or str(uuid.uuid4())
        request.request_id = request_id
        request._request_id_token = set_request_id(request_id)

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id:
            response[self.response_header] = request_id
        token = getattr(request, "_request_id_token", None)
        if token is not None:
            reset_request_id(token)
        return response
