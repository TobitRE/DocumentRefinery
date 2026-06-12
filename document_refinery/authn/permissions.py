from rest_framework.permissions import BasePermission

from .models import APIKey


class APIKeyRequired(BasePermission):
    message = "Valid API key required."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, APIKey)


class HasScope(BasePermission):
    message = "API key scope is insufficient."

    def has_permission(self, request, view) -> bool:
        api_key = request.auth
        if not isinstance(api_key, APIKey):
            return False

        required_scopes = getattr(view, "required_scopes", None)
        if not required_scopes:
            return True

        scope_set = set(api_key.scopes or [])
        return all(scope in scope_set for scope in required_scopes)


class StaffOrAPIKey(BasePermission):
    message = "Valid staff session or API key required."

    def has_permission(self, request, view) -> bool:
        if isinstance(request.auth, APIKey):
            return True
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_staff)
