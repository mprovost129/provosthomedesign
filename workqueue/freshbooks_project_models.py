"""Explicitly approved project provisioning; independent of invoices and payments."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class FreshBooksProject(models.Model):
    class State(models.TextChoices):
        QUEUED = 'queued', 'Waiting for FreshBooks'
        WORKING = 'working', 'Preparing FreshBooks project'
        LINKED = 'linked', 'Connected to FreshBooks'
        ATTENTION = 'attention', 'FreshBooks needs attention'
        UNKNOWN = 'unknown', 'Check FreshBooks before retrying'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField('WorkProject', on_delete=models.PROTECT, related_name='freshbooks_project')
    work_item = models.ForeignKey('WorkItem', on_delete=models.PROTECT)
    client = models.ForeignKey('Client', on_delete=models.PROTECT)
    account_id = models.CharField(max_length=50)
    business_id = models.PositiveBigIntegerField()
    customer_id = models.PositiveBigIntegerField(null=True)
    remote_id = models.PositiveBigIntegerField(null=True)
    title = models.CharField(max_length=255)
    marker = models.CharField(max_length=80, unique=True)
    payload = models.JSONField(default=dict)
    client_snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    phase = models.CharField(max_length=12, default='client')
    attempted_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['business_id','remote_id'],name='wq_fb_remote_project_unique'),
            models.CheckConstraint(condition=Q(state__in=['queued','working','linked','attention','unknown']),name='wq_fb_project_state')]


class FreshBooksClientProvision(models.Model):
    """A persisted write intent prevents two projects creating the same billing client."""
    client = models.ForeignKey('Client', on_delete=models.PROTECT)
    account_id = models.CharField(max_length=50)
    payload = models.JSONField()
    marker = models.CharField(max_length=80, unique=True)
    state = models.CharField(max_length=12, default='queued')
    remote_id = models.PositiveBigIntegerField(null=True)
    attempted_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_id','client'],name='wq_fb_client_provision_unique'),
            models.CheckConstraint(condition=Q(state__in=['queued','creating','linked','attention','unknown']),name='wq_fb_client_provision_state')]
