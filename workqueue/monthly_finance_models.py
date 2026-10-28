"""Append-only sandbox financial evidence, separate from invoice principal."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class NativeMonthState(models.Model):
    month = models.DateField(primary_key=True)
    account_id = models.CharField(max_length=255, blank=True)
    generation = models.PositiveBigIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    checked_at = models.DateTimeField(null=True, blank=True)
    issue = models.CharField(max_length=40, blank=True)


class NativeMonthSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    month = models.ForeignKey(NativeMonthState, on_delete=models.PROTECT, related_name='snapshots')
    account_id = models.CharField(max_length=255)
    rows = models.JSONField(default=list)
    checksum = models.CharField(max_length=64)
    history_changed = models.BooleanField(default=False)
    captured_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-captured_at', '-id']


class NativeFinancialReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    month = models.ForeignKey(NativeMonthState, on_delete=models.PROTECT, related_name='reviews')
    source = models.ForeignKey(NativeMonthSnapshot, on_delete=models.PROTECT)
    report = models.JSONField()
    checksum = models.CharField(max_length=64)
    note = models.TextField(max_length=2000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']

