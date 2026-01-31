from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication

from .models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        try:
            keyword, raw_key = header.split(" ", 1)
        except ValueError:
            return None
        if keyword != self.keyword:
            return None

        api_key = APIKey.lookup_from_raw(raw_key.strip())
        if not api_key:
            return None

        self._touch_last_used(api_key)
        request.api_key = api_key
        return (None, api_key)

    def authenticate_header(self, request):
        return self.keyword

    @staticmethod
    def _touch_last_used(api_key: APIKey) -> None:
        now = timezone.now()
        if api_key.last_used_at and api_key.last_used_at > now - timedelta(hours=1):
            return
        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
