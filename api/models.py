import secrets
from django.db import models
from django.utils import timezone


class PartnerAPIKey(models.Model):
    name = models.CharField(max_length=100, help_text="Partner site name")
    key = models.CharField(max_length=64, unique=True, editable=False)
    allowed_origins = models.TextField(
        blank=True,
        help_text=(
            "Comma-separated allowed origins, e.g. https://partnersite.com. "
            "Leave blank to allow any origin."
        ),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Partner API Key"
        verbose_name_plural = "Partner API Keys"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = "phd_" + secrets.token_urlsafe(40)
        super().save(*args, **kwargs)

    def get_allowed_origins(self):
        if not self.allowed_origins:
            return []
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def is_origin_allowed(self, origin):
        allowed = self.get_allowed_origins()
        if not allowed:
            return True
        return origin in allowed
