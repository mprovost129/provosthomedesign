"""Private project planning and immutable client-facing proposal versions."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .models import Client, WorkItem, WorkProject, NotificationDelivery


class ProjectPlan(models.Model):
    project = models.OneToOneField(WorkProject, on_delete=models.PROTECT, related_name='plan')
    scope = models.TextField(blank=True, max_length=10000)
    internal_notes = models.TextField(blank=True, max_length=10000)
    target_date = models.DateField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(estimated_hours__gte=0) | Q(estimated_hours__isnull=True), name='wq_plan_hours_nonnegative')]


class ProjectTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name='tasks')
    title = models.CharField(max_length=200)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, max_length=5000)
    completed_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['completed_at', 'due_date', 'created_at']


class ProposalTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    scope = models.TextField(blank=True, max_length=10000)
    deliverables = models.TextField(blank=True, max_length=10000)
    revisions = models.TextField(blank=True, max_length=5000)
    exclusions = models.TextField(blank=True, max_length=5000)
    terms = models.TextField(blank=True, max_length=10000)
    archived = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['name', 'id']


class ProposalSequence(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_number = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='wq_proposal_sequence_singleton')]


class Proposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=30, unique=True)
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name='proposals')
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='proposals')
    source_item = models.ForeignKey(WorkItem, null=True, blank=True, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    draft = models.JSONField(default=dict)
    has_draft = models.BooleanField(default=True)
    current_revision = models.ForeignKey('ProposalRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    @property
    def status_label(self):
        return 'Draft revision' if self.has_draft and self.current_revision_id else 'Draft' if self.has_draft else self.current_revision.get_state_display()

    @property
    def document_label(self):
        data = self.draft if self.has_draft else self.current_revision.snapshot if self.current_revision_id else {}
        return 'Estimate' if data.get('document_kind') == 'estimate' else 'Proposal'


class ProposalRevision(models.Model):
    class State(models.TextChoices):
        SENT = 'sent', 'Sent'
        VIEWED = 'viewed', 'Viewed'
        ACCEPTED = 'accepted', 'Accepted'
        CHANGES = 'changes', 'Changes requested'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(Proposal, on_delete=models.PROTECT, related_name='revisions')
    sequence = models.PositiveIntegerField()
    snapshot = models.JSONField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.SENT)
    recipient = models.EmailField()
    sent_at = models.DateTimeField(default=timezone.now)
    viewed_at = models.DateTimeField(null=True)
    accepted_at = models.DateTimeField(null=True)
    accepted_name = models.CharField(max_length=200, blank=True)
    response_note = models.TextField(blank=True, max_length=5000)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-sequence']
        constraints = [models.UniqueConstraint(fields=['proposal', 'sequence'], name='wq_proposal_revision_unique'),
                      models.CheckConstraint(condition=Q(state__in=['sent', 'viewed', 'accepted', 'changes', 'withdrawn']), name='wq_proposal_revision_state')]


class ProposalInvitation(models.Model):
    revision = models.OneToOneField(ProposalRevision, on_delete=models.PROTECT, related_name='invitation')
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    notification = models.OneToOneField(NotificationDelivery, on_delete=models.PROTECT)


class ProjectEvent(models.Model):
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name='planning_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ['-created_at', '-id']
