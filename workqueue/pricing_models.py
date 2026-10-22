"""Versioned staff pricing; saved calculations never follow later rate changes."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class PricingRateVersion(models.Model):
    category = models.CharField(max_length=20)
    sequence = models.PositiveIntegerField()
    label = models.CharField(max_length=120)
    rates = models.JSONField()
    source_note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['category', '-sequence']
        constraints = [
            models.UniqueConstraint(fields=['category', 'sequence'], name='wq_pricing_rate_version'),
            models.CheckConstraint(condition=models.Q(category__in=['homeowner', 'contractor', 'other']), name='wq_pricing_rate_category'),
        ]

    def __str__(self):
        return f'{self.label} · version {self.sequence}'


class PricingSequence(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='wq_pricing_sequence_singleton')]


class PricingEstimate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=200)
    client = models.ForeignKey('Client', null=True, blank=True, on_delete=models.PROTECT, related_name='pricing_estimates')
    project = models.ForeignKey('WorkProject', null=True, blank=True, on_delete=models.PROTECT, related_name='pricing_estimates')
    current_revision = models.ForeignKey('PricingEstimateRevision', null=True, on_delete=models.PROTECT, related_name='+')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']


class PricingEstimateRevision(models.Model):
    estimate = models.ForeignKey(PricingEstimate, on_delete=models.PROTECT, related_name='revisions')
    sequence = models.PositiveIntegerField()
    rate_version = models.ForeignKey(PricingRateVersion, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    proposal = models.OneToOneField('Proposal', null=True, blank=True, on_delete=models.PROTECT, related_name='pricing_calculation')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sequence']
        constraints = [models.UniqueConstraint(fields=['estimate', 'sequence'], name='wq_pricing_estimate_revision')]
