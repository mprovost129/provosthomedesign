"""Operational metadata only: never request contents, clients or credentials."""
import uuid
from django.db import models
from django.utils import timezone


def incident_reference():
    return 'ERR-' + uuid.uuid4().hex[:16].upper()


class OperationalIncident(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True, default=incident_reference, editable=False)
    fingerprint = models.CharField(max_length=64, unique=True)
    area = models.CharField(max_length=200)
    method = models.CharField(max_length=10)
    error_type = models.CharField(max_length=150)
    location = models.CharField(max_length=200, blank=True)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    occurrences = models.PositiveIntegerField(default=0)
    reported_count = models.PositiveIntegerField(default=0)
    reviewed_count = models.PositiveIntegerField(default=0)
    reviewed_at = models.DateTimeField(null=True, blank=True)


class OperationsSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    email_alerts = models.BooleanField(default=True)
    last_queued_at = models.DateTimeField(null=True, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_delivery = models.ForeignKey('NotificationDelivery', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    last_test_delivery = models.ForeignKey('NotificationDelivery', null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    version = models.PositiveIntegerField(default=1)
