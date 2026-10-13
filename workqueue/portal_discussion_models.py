"""Discussion is separate from the immutable submitted review and marked PDF."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ReviewDiscussion(models.Model):
    review = models.OneToOneField('PlanReview', primary_key=True, on_delete=models.PROTECT, related_name='discussion')
    read_through = models.PositiveBigIntegerField(default=0)


class ReviewNoteState(models.Model):
    review = models.ForeignKey('PlanReview', on_delete=models.PROTECT, related_name='note_states')
    # Empty string denotes the general notes; other values are submitted annotation UUIDs.
    note = models.CharField(max_length=36, blank=True)
    addressed = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    changed_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['review', 'note'], name='wq_review_note_unique'),
            models.CheckConstraint(condition=Q(version__gte=1), name='wq_review_note_version_positive')]


class ReviewMessage(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    review = models.ForeignKey('PlanReview', on_delete=models.PROTECT, related_name='discussion_messages')
    note = models.CharField(max_length=36, blank=True)
    body = models.TextField()
    author_name = models.CharField(max_length=200)
    member = models.ForeignKey('PortalMember', null=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    from_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    delivery = models.OneToOneField('NotificationDelivery', null=True, on_delete=models.PROTECT, related_name='review_message')

    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(condition=(Q(from_staff=True, member__isnull=True)
            | Q(from_staff=False, member__isnull=False, actor__isnull=True)), name='wq_review_message_identity')]
