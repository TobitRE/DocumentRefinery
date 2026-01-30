import uuid

from django.db import models


class BaseModel(models.Model):
    """
    Base model, contains all fields models should have
    """

    uuid = models.UUIDField(
        unique=True,
        editable=False,
        default=uuid.uuid4,
        verbose_name="Public Identifier",
    )
    created_at = models.DateTimeField(
        "Created at", null=True, blank=True, auto_now_add=True
    )
    modified_at = models.DateTimeField(
        "Last modified", null=True, blank=True, auto_now=True
    )

    def save(self, *args, **kwargs):
        skip_full_clean = kwargs.pop("skip_full_clean", False)
        if not skip_full_clean:
            self.full_clean()  # Ensures model validation on save
        super().save(*args, **kwargs)

    class Meta:
        abstract = True

# Create your models here.
