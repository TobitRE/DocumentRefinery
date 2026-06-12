try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension
except ImportError:  # pragma: no cover - drf-spectacular is an optional import at startup.
    OpenApiAuthenticationExtension = None


if OpenApiAuthenticationExtension is not None:

    class APIKeyAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = "authn.authentication.APIKeyAuthentication"
        name = "ApiKeyAuth"

        def get_security_definition(self, auto_schema):
            return {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Use `Api-Key <token>`.",
            }
