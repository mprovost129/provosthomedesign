"""Explicit client portal grants and immutable published document versions."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ProjectPortal(models.Model):
    project = models.OneToOneField('WorkProject', on_delete=models.PROTECT, related_name='portal')
    version = models.PositiveIntegerField(default=1)


class ClientProject(models.Model):
    """A reviewed CRM relationship; does not itself grant portal access."""
    client = models.ForeignKey('Client', on_delete=models.PROTECT, related_name='project_links')
    project = models.ForeignKey('WorkProject', on_delete=models.PROTECT, related_name='client_links')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['client', 'project'], name='wq_client_project_unique')]


class PortalMember(models.Model):
    project = models.ForeignKey('WorkProject', on_delete=models.PROTECT, related_name='portal_members')
    client = models.ForeignKey('Client', on_delete=models.PROTECT)
    contact = models.ForeignKey('ClientContact', on_delete=models.PROTECT)
    # Frozen at approval: editing/merging CRM contacts must not grant a new identity access.
    email = models.EmailField()
    financial_access = models.BooleanField(default=False)
    review_access = models.BooleanField(default=False)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'email'], name='wq_portal_project_email_unique')]
        ordering = ['email']


class PortalDocument(models.Model):
    class Kind(models.TextChoices):
        PLAN = 'plan', 'Plans'
        DOCUMENT = 'document', 'Documents'
        INVOICE = 'invoice', 'Invoice documents'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('WorkProject', on_delete=models.PROTECT, related_name='portal_documents')
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    billing_client = models.ForeignKey('Client', null=True, blank=True, on_delete=models.PROTECT)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['kind', 'title', 'id']
        constraints = [models.CheckConstraint(condition=(
            Q(kind='invoice', billing_client__isnull=False)
            | Q(kind__in=['plan', 'document'], billing_client__isnull=True)), name='wq_portal_document_audience')]


class PortalDocumentVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(PortalDocument, on_delete=models.PROTECT, related_name='revisions')
    sequence = models.PositiveIntegerField()
    note = models.CharField(max_length=1000, blank=True)
    upload = models.OneToOneField('PendingUpload', null=True, blank=True, on_delete=models.PROTECT)
    external_url = models.URLField(max_length=1000, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    storage_key = models.CharField(max_length=1024, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    published_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-sequence']
        constraints = [
            models.UniqueConstraint(fields=['document', 'sequence'], name='wq_portal_document_version_unique'),
            models.CheckConstraint(condition=Q(sequence__gte=1), name='wq_portal_sequence_positive'),
            models.CheckConstraint(condition=(
                Q(upload__isnull=False, external_url='') & ~Q(storage_key='')
                | Q(upload__isnull=True, storage_key='') & ~Q(external_url='')),
                name='wq_portal_version_source'),
        ]
