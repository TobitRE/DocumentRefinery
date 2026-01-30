import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import models

from core.models import BaseModel


class Tenant(BaseModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class APIKey(BaseModel):
    KEY_PREFIX_LEN = 8

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    prefix = models.CharField(max_length=KEY_PREFIX_LEN, editable=False, blank=True)
    key_hash = models.CharField(max_length=64, editable=False, unique=True)
    scopes = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    docling_options_json = models.JSONField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"

    @classmethod
    def _hash_key(cls, raw_key: str) -> str:
        secret = settings.SECRET_KEY.encode("utf-8")
        return hmac.new(secret, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()

    @classmethod
    def generate_key(cls) -> tuple[str, str, str]:
        raw_key = secrets.token_urlsafe(32)
        prefix = raw_key[: cls.KEY_PREFIX_LEN]
        key_hash = cls._hash_key(raw_key)
        return raw_key, prefix, key_hash

    @classmethod
    def lookup_from_raw(cls, raw_key: str):
        key_hash = cls._hash_key(raw_key)
        return cls.objects.filter(key_hash=key_hash, active=True).first()

# Create your models here.
