"""Source evidence and comparisons only; never a second payment ledger."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class FreshBooksPaymentExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=50)
    business_id = models.PositiveBigIntegerField()
    business_name = models.CharField(max_length=200)
    month = models.DateField()
    filename = models.CharField(max_length=150)
    file_hash = models.CharField(max_length=64)
    source = models.JSONField()
    source_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['account_id', 'month', 'file_hash'], name='wq_fb_payment_export_unique')]


class FreshBooksPaymentCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence = models.ForeignKey(FreshBooksPaymentExport, on_delete=models.PROTECT, related_name='checks')
    result = models.JSONField()
    result_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at']
