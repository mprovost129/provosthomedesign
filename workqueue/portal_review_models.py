"""A review belongs to one immutable plan version and one approved identity."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PlanReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('PortalMember', on_delete=models.PROTECT, related_name='plan_reviews')
    document_version = models.ForeignKey('PortalDocumentVersion', on_delete=models.PROTECT, related_name='plan_reviews')
    client = models.ForeignKey('Client', on_delete=models.PROTECT, related_name='plan_reviews')
    contact = models.ForeignKey('ClientContact', on_delete=models.PROTECT, related_name='plan_reviews')
    author_email = models.EmailField()
    author_name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=200)
    grant_at = models.DateTimeField()
    version = models.PositiveIntegerField(default=1)
    summary = models.TextField(blank=True)
    annotations = models.JSONField(default=list, blank=True)
    # Coordinates are fractions of PDF.js's unzoomed, intrinsically rotated page viewport.
    geometry = models.JSONField(default=list, blank=True)
    document_title = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    work_item = models.OneToOneField('WorkItem', on_delete=models.PROTECT, null=True, blank=True, related_name='plan_review')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['member', 'document_version', 'grant_at'],
                condition=Q(submitted_at__isnull=True), name='wq_one_plan_review_draft'),
            models.CheckConstraint(condition=(Q(submitted_at__isnull=True, work_item__isnull=True)
                | Q(submitted_at__isnull=False, work_item__isnull=False)), name='wq_review_submission_pair'),
            models.CheckConstraint(condition=Q(version__gte=1), name='wq_review_version_positive'),
        ]
