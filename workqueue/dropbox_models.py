"""Private filing destinations; independent of client portal permissions."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class DropboxConnection(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    account_id = models.CharField(max_length=200, blank=True)
    account_email = models.EmailField(blank=True)
    namespace_id = models.CharField(max_length=100, blank=True)
    encrypted_tokens = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True)
    root_id = models.CharField(max_length=200, blank=True)
    root_path = models.CharField(max_length=500, default='/Projects')
    enabled = models.BooleanField(default=False)
    indexed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='wq_dropbox_singleton')]


class DropboxFolder(models.Model):
    """Read-only index of immediate children under the approved Projects root."""
    id = models.CharField(primary_key=True, max_length=200)
    name = models.CharField(max_length=255)
    path = models.CharField(max_length=1024)
    number = models.CharField(max_length=6, blank=True, db_index=True)
    available = models.BooleanField(default=True)
    indexed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-number', 'name']

    def __str__(self):
        return self.name


class ProjectFolder(models.Model):
    project = models.OneToOneField('WorkProject', on_delete=models.PROTECT, related_name='filing_folder')
    number = models.CharField(max_length=6, unique=True, null=True, blank=True)
    folder = models.OneToOneField(DropboxFolder, null=True, blank=True, on_delete=models.PROTECT, related_name='project_binding')
    intended_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now)


class ProjectMonthSequence(models.Model):
    period = models.CharField(primary_key=True, max_length=4)
    last_number = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(last_number__lte=99), name='wq_project_month_limit')]


class DropboxFiling(models.Model):
    class State(models.TextChoices):
        QUEUED = 'queued', 'Queued for transfer'
        COPYING = 'copying', 'Transferring'
        FILED = 'filed', 'Filed in Dropbox'
        ATTENTION = 'attention', 'Needs attention'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.OneToOneField('Submission', on_delete=models.PROTECT, related_name='dropbox_filing')
    destination = models.ForeignKey(ProjectFolder, on_delete=models.PROTECT, related_name='filings')
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    batch_id = models.CharField(max_length=200, blank=True)
    batch_name = models.CharField(max_length=100)
    last_error = models.CharField(max_length=500, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        ordering = ['approved_at', 'pk']
        constraints = [models.CheckConstraint(condition=Q(state__in=['queued','copying','filed','attention']), name='wq_filing_state')]


class DropboxFiledFile(models.Model):
    filing = models.ForeignKey(DropboxFiling, on_delete=models.PROTECT, related_name='files')
    attachment = models.OneToOneField('Attachment', on_delete=models.PROTECT, related_name='dropbox_copy')
    name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=1024)
    size_bytes = models.PositiveBigIntegerField()
    dropbox_id = models.CharField(max_length=200, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    filed_at = models.DateTimeField(null=True)
