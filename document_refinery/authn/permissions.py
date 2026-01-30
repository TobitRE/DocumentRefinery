from rest_framework.permissions import BasePermission

from .models import APIKey


class APIKeyRequired(BasePermission):
    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, APIKey)


class HasScope(BasePermission):
    def has_permission(self, request, view) -> bool:
        api_key = request.auth
        if not isinstance(api_key, APIKey):
            return False

        required_scopes = getattr(view, "required_scopes", None)
        if not required_scopes:
            return True

        scope_set = set(api_key.scopes or [])
        return all(scope in scope_set for scope in required_scopes)
