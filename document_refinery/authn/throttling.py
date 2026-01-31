from rest_framework.throttling import SimpleRateThrottle


class APIKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        api_key = getattr(request, "api_key", None)
        if not api_key:
            return None
        return f"api_key:{api_key.prefix}"
