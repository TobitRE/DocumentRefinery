from django.apps import AppConfig


class AuthnConfig(AppConfig):
    name = 'authn'

    def ready(self):
        from . import schema  # noqa: F401
