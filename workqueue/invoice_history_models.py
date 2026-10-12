"""Immutable exported invoice facts, separate from live billing and queue jobs."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from .models import Client, WorkProject


class InvoiceCSVImport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    account_id = models.CharField(max_length=50)
    business_name = models.CharField(max_length=200)
    filename = models.CharField(max_length=255)
    source_hash = models.CharField(max_length=64)
    invoices = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)
    applied_at = models.DateTimeField(null=True)
    removed_at = models.DateTimeField(null=True)
    result = models.JSONField(default=dict)


class HistoricalInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(InvoiceCSVImport, on_delete=models.PROTECT, related_name='records')
    account_id = models.CharField(max_length=50)
    number = models.CharField(max_length=100)
    exported_client = models.CharField(max_length=200)
    issued = models.DateField()
    due = models.DateField(null=True)
    paid_date = models.DateField(null=True)
    exported_status = models.CharField(max_length=50)
    currency = models.CharField(max_length=3)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)
    lines = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.PROTECT, related_name='historical_invoices')
    project = models.ForeignKey(WorkProject, null=True, blank=True, on_delete=models.PROTECT, related_name='historical_invoices')
    version = models.PositiveIntegerField(default=1)
    removed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_id', 'number'], name='wq_history_invoice_unique')]


class InvoiceHistoryEvent(models.Model):
    invoice = models.ForeignKey(HistoricalInvoice, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    changes = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)
