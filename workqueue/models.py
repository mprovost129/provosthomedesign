"""Project history and independent work requests; no public access by ID alone."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone


class Status(models.TextChoices):
    NEW = "new", "New"
    NEEDS_INFORMATION = "needs_information", "Needs Information"
    READY = "ready", "Ready to Schedule"
    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In Progress"
    ON_HOLD = "on_hold", "On Hold"
    COMPLETED = "completed", "Completed"
    CANCELED = "canceled", "Canceled"
    DECLINED = "declined", "Declined"
    DUPLICATE = "duplicate", "Duplicate"


CLOSED_STATUSES = (Status.COMPLETED, Status.CANCELED, Status.DECLINED, Status.DUPLICATE)


class Priority(models.TextChoices):
    NORMAL = "normal", "Normal"
    SOON = "soon", "Soon"
    URGENT = "urgent", "Urgent"


class WorkProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    street = models.CharField(max_length=250, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    canonical_reference = models.CharField(max_length=32, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.canonical_reference or 'Unnumbered project'} — {self.name}"


class ProjectAccess(models.Model):
    """Granted access, independent of unverified submitted contact information."""
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name="access_grants")
    email = models.EmailField()
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "email"], name="wq_project_email_unique")]


class QueueState(models.Model):
    """Singleton allocator and queue lock. Import must set the legacy high-water mark."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_reference_number = models.PositiveBigIntegerField(default=0)
    last_queue_order = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name="wq_singleton_state")]


class LegacyQueueImport(models.Model):
    """Private, lossless source archive, including rows omitted from the work queue."""
    digest = models.CharField(primary_key=True, max_length=64, editable=False)
    source_id = models.CharField(max_length=200)
    payload = models.JSONField()
    summary = models.JSONField(default=dict)
    imported_at = models.DateTimeField(default=timezone.now, editable=False)


class WorkItemQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status__in=CLOSED_STATUSES)

    def with_position(self):
        # Count against the full active queue, before search/pagination/status filters.
        preceding = self.model.objects.active().filter(
            Q(queue_order__lt=OuterRef("queue_order"))
            | Q(queue_order=OuterRef("queue_order"), received_at__lt=OuterRef("received_at"))
            | Q(queue_order=OuterRef("queue_order"), received_at=OuterRef("received_at"),
                id__lt=OuterRef("id"))
        ).order_by().annotate(group=Value(1)).values("group").annotate(total=Count("id")).values("total")
        return self.annotate(requests_ahead=Coalesce(Subquery(preceding, output_field=models.IntegerField()), 0))


class WorkItem(models.Model):
    class Kind(models.TextChoices):
        NEW = "new", "New Submission"
        UPDATE = "update", "Update"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=32, unique=True, editable=False)
    project = models.ForeignKey(WorkProject, null=True, blank=True, on_delete=models.PROTECT,
                               related_name="work_items")
    previous_request = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="updates")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NEW)
    project_context = models.CharField(max_length=500, blank=True)
    contact_full_name = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=50)
    billing_street = models.CharField(max_length=250, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_state = models.CharField(max_length=50, blank=True)
    billing_zip = models.CharField(max_length=20, blank=True)
    project_name = models.CharField(max_length=200, blank=True)
    project_street = models.CharField(max_length=250, blank=True)
    project_city = models.CharField(max_length=100, blank=True)
    project_state = models.CharField(max_length=50, blank=True)
    project_zip = models.CharField(max_length=20, blank=True)
    service_needed = models.CharField(max_length=100, blank=True)
    description = models.TextField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NEW)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    queue_order = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    received_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    estimated_days = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True,
                                         validators=[MinValueValidator(0)])
    requested_deadline = models.DateField(null=True, blank=True)
    committed_due_date = models.DateField(null=True, blank=True)
    scheduled_start = models.DateField(null=True, blank=True)
    estimated_completion = models.DateField(null=True, blank=True)
    followup_date = models.DateField(null=True, blank=True)
    internal_notes = models.TextField(blank=True)
    source = models.CharField(max_length=100, blank=True)
    legacy_ref = models.CharField(max_length=200, blank=True)
    submission_position = models.PositiveIntegerField(null=True, blank=True, editable=False)
    objects = WorkItemQuerySet.as_manager()

    class Meta:
        ordering = ["queue_order", "received_at", "id"]
        indexes = [models.Index(fields=["status", "queue_order"], name="wq_status_order_idx"),
                   models.Index(fields=["queue_order", "received_at", "id"], name="wq_order_received_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(queue_order__gte=1), name="wq_order_positive"),
            models.CheckConstraint(condition=Q(status__in=Status.values), name="wq_valid_status"),
            models.CheckConstraint(condition=Q(priority__in=Priority.values), name="wq_valid_priority"),
            models.CheckConstraint(condition=Q(kind__in=["new", "update"]), name="wq_valid_kind"),
        ]

    @property
    def is_active(self):
        return self.status not in CLOSED_STATUSES

    def clean(self):
        super().clean()
        if self.previous_request_id == self.pk:
            raise ValidationError({"previous_request": "A request cannot be its own previous request."})
        if self.scheduled_start and self.estimated_completion and self.estimated_completion < self.scheduled_start:
            raise ValidationError({"estimated_completion": "Estimated completion cannot precede the scheduled start."})

    @property
    def position(self):
        return self.requests_ahead + 1 if self.is_active and hasattr(self, "requests_ahead") else None

    @property
    def needs_project_link(self):
        return self.kind == self.Kind.UPDATE and self.project_id is None

    def __str__(self):
        return f"{self.reference} — {self.contact_full_name}"


class Submission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name="submissions")
    idempotency_key = models.CharField(max_length=200, unique=True)
    channel = models.CharField(max_length=30, default="staff")
    answers = models.JSONField(default=dict)
    owner_email = models.EmailField(blank=True, db_index=True)
    received_at = models.DateTimeField(default=timezone.now)


class Attachment(models.Model):
    """Private object metadata. Downloading is exclusively through an authorized view."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT, related_name="attachments")
    original_name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=1024, blank=True)
    legacy_drive_id = models.CharField(max_length=200, blank=True)
    content_type = models.CharField(max_length=150, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    categories = models.JSONField(default=list)
    uploaded_at = models.DateTimeField(default=timezone.now)


class AuditEvent(models.Model):
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class NotificationDelivery(models.Model):
    """Durable outbox. Unknown send outcomes must never be automatically retried."""
    submission = models.ForeignKey(Submission, null=True, blank=True, on_delete=models.PROTECT, related_name="notifications")
    appointment = models.ForeignKey("Appointment", null=True, blank=True, on_delete=models.PROTECT, related_name="notifications")
    appointment_event = models.CharField(max_length=20, blank=True)
    recipient_kind = models.CharField(max_length=10, choices=[("client", "Client"), ("owner", "Owner"), ("signin", "Sign-in")])
    recipient = models.EmailField()
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(max_length=20, choices=[("pending", "Pending"), ("sending", "Sending"),
                            ("sent", "Sent"), ("unknown", "Needs reconciliation"), ("failed", "Failed")],
                            default="pending")
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["submission", "recipient_kind"],
                                              name="wq_submission_notice_unique"),
                       models.UniqueConstraint(fields=["appointment", "appointment_event", "recipient_kind"],
                                              name="wq_appointment_notice_unique")]


class EmailAccessToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    digest = models.CharField(max_length=64, unique=True)
    reference = models.CharField(max_length=32, blank=True)
    destination = models.CharField(max_length=10, default="tracking", choices=[("tracking", "Tracking"), ("booking", "Booking")])
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class ProjectClaim(models.Model):
    """A new project's original intake claim; verification never claims an existing project."""
    project = models.OneToOneField(WorkProject, on_delete=models.PROTECT, related_name="intake_claim")
    submission = models.OneToOneField(Submission, on_delete=models.PROTECT)
    email = models.EmailField()
    verified_at = models.DateTimeField(null=True, blank=True)


class PendingUpload(models.Model):
    class State(models.TextChoices):
        RESERVED = "reserved", "Uploading"
        READY = "ready", "Ready"
        ATTACHED = "attached", "Submitted"
        FAILED = "failed", "Upload failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake_nonce = models.UUIDField(db_index=True)
    session_digest = models.CharField(max_length=64)
    original_name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=1024, unique=True)
    upload_key = models.CharField(max_length=1024, blank=True)
    sealed_key = models.CharField(max_length=1024, blank=True)
    size_bytes = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=150)
    state = models.CharField(max_length=12, choices=State.choices, default=State.RESERVED)
    attachment = models.OneToOneField(Attachment, null=True, blank=True, on_delete=models.PROTECT)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(default=timezone.now)


class BookingState(models.Model):
    """Seeded singleton: reservations serialize independently of queue edits."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)


class BookingAccess(models.Model):
    email = models.EmailField(unique=True)
    allowed = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class Appointment(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Confirming with calendar"
        SYNCING = "syncing", "Confirming with calendar"
        CONFIRMED = "confirmed", "Confirmed"
        ATTENTION = "attention", "Calendar check needed"
        CANCELED = "canceled", "Canceled"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True)
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=50)
    purpose = models.TextField()
    project = models.ForeignKey(WorkProject, null=True, blank=True, on_delete=models.PROTECT, related_name="appointments")
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacements")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    reserved_until = models.DateTimeField()
    local_date = models.DateField(db_index=True)
    calendar_id = models.CharField(max_length=255)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    cancel_requested = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    sync_started_at = models.DateTimeField(null=True, blank=True)
    sync_token = models.UUIDField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["starts_at", "id"]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=models.F("starts_at")), name="wq_meeting_duration_positive"),
                       models.CheckConstraint(condition=Q(reserved_until__gt=models.F("ends_at")), name="wq_meeting_buffer_positive"),
                       models.UniqueConstraint(fields=["previous"], condition=Q(state__in=["pending", "syncing", "confirmed", "attention"]),
                                               name="wq_one_active_reschedule"),
                       models.CheckConstraint(condition=Q(state__in=["pending", "syncing", "confirmed", "attention", "canceled", "completed"]), name="wq_valid_appointment_state"),
                       models.CheckConstraint(condition=Q(ends_at=models.F("starts_at")+timedelta(minutes=30)), name="wq_30_minute_meeting"),
                       models.CheckConstraint(condition=Q(reserved_until=models.F("ends_at")+timedelta(minutes=30)), name="wq_30_minute_buffer")]

    @property
    def event_ids(self):
        return ["phd" + self.pk.hex + "a", "phd" + self.pk.hex + "b"]

    @property
    def reference(self):
        return "APT-" + self.pk.hex[:12].upper()

    @property
    def has_pending_reschedule(self):
        return any(item.state in ("pending", "syncing", "confirmed", "attention") for item in self.replacements.all())

    @property
    def can_cancel(self):
        return (self.starts_at > timezone.now() and self.state in ("pending", "confirmed", "attention")
                and not self.cancel_requested and not self.has_pending_reschedule)


class BookingAudit(models.Model):
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
