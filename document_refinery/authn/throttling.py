from rest_framework.throttling import SimpleRateThrottle

from .models import APIKey


class APIKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        api_key = getattr(request, "api_key", None)
        if not api_key and isinstance(getattr(request, "auth", None), APIKey):
            api_key = request.auth
        if api_key:
            return f"api_key:{api_key.key_hash}"
        ident = self.get_ident(request)
        if not ident:
            return None
        return f"api_key:anon:{ident}"
