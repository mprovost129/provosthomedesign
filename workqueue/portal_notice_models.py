"""Reviewed portal invitations and bounded opt-in review reminders."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PortalInvitationBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('WorkProject', on_delete=models.PROTECT)
    revision = models.ForeignKey('PortalDocumentVersion', null=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)


class PortalInvitation(models.Model):
    batch = models.ForeignKey(PortalInvitationBatch, on_delete=models.PROTECT, related_name='invitations')
    member = models.ForeignKey('PortalMember', on_delete=models.PROTECT)
    client = models.ForeignKey('Client', on_delete=models.PROTECT)
    contact = models.ForeignKey('ClientContact', on_delete=models.PROTECT)
    email = models.EmailField()
    grant_at = models.DateTimeField()
    delivery = models.OneToOneField('NotificationDelivery', on_delete=models.PROTECT, related_name='portal_invitation')
    review_requested = models.BooleanField(default=False)
    reminders_enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'member'], name='wq_portal_invitation_recipient'),
            models.CheckConstraint(condition=Q(version__gte=1), name='wq_portal_invitation_version'),
            models.CheckConstraint(condition=Q(reminders_enabled=False) | Q(review_requested=True), name='wq_portal_reminders_review_only')]


class PortalReviewReminder(models.Model):
    invitation = models.ForeignKey(PortalInvitation, on_delete=models.PROTECT, related_name='reminders')
    number = models.PositiveSmallIntegerField()
    delivery = models.OneToOneField('NotificationDelivery', on_delete=models.PROTECT, related_name='portal_review_reminder')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['invitation', 'number'], name='wq_portal_review_reminder_unique'),
            models.CheckConstraint(condition=Q(number__in=[1, 2]), name='wq_portal_review_reminder_limit')]
