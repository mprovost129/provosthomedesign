"""One reusable export of each frozen submitted review, with an explicit worker lease."""
import uuid
from django.db import models
from django.db.models import Q
from django.utils import timezone

class PlanReviewExport(models.Model):
    review = models.OneToOneField('PlanReview', on_delete=models.PROTECT, primary_key=True, related_name='marked_export')
    state = models.CharField(max_length=12, default='pending', choices=[(name,name.title()) for name in ['pending','processing','ready','failed']])
    attempt_token = models.UUIDField(default=uuid.uuid4)
    attempts = models.PositiveIntegerField(default=0)
    storage_key = models.CharField(max_length=1024, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(state__in=['pending','processing','ready','failed']),name='wq_valid_review_export_state')]
        indexes = [models.Index(fields=['state','created_at'],name='wq_review_export_pending')]
