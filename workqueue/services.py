"""All queue writes use one lock order: singleton queue state, then work item.

PostgreSQL row locks protect allocation, receipt snapshots and concurrent edits.
SQLite is suitable for the isolated preview, not evidence of production locking.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from .models import AuditEvent, QueueState, Submission, WorkItem, WorkProject


class EditConflict(Exception):
    pass


def lock_queue():
    # Seeded by migration so the first two concurrent submissions cannot race to create it.
    return QueueState.objects.select_for_update().get(pk=1)


def audit_value(value):
    return str(value) if value is not None else None


@transaction.atomic
def create_work(*, data, actor, idempotency_key=None):
    state = lock_queue()
    key = idempotency_key or f"staff:{uuid.uuid4()}"
    existing = Submission.objects.select_related("work_item").filter(idempotency_key=key).first()
    if existing:
        return existing.work_item, False

    data = dict(data)
    project = data.pop("project", None)
    previous = data.pop("previous_request", None)
    if previous and (not project or previous.project_id != project.pk):
        raise ValidationError("The previous request must belong to the connected project.")
    maximum = WorkItem.objects.aggregate(value=Max("queue_order"))["value"] or 0
    state.last_reference_number += 1
    state.last_queue_order = max(state.last_queue_order, maximum) + 1
    item = WorkItem(reference=f"PHD-{state.last_reference_number:05d}",
                    queue_order=state.last_queue_order, project=project,
                    previous_request=previous, **data)
    item.full_clean()
    if item.kind == WorkItem.Kind.NEW and project is None:
        project = WorkProject.objects.create(
            name=item.project_name or item.project_street or item.project_context or item.reference,
            street=item.project_street, city=item.project_city, state=item.project_state,
            zip_code=item.project_zip, canonical_reference=item.reference,
        )
        item.project = project
    item.save()
    item.submission_position = WorkItem.objects.active().count() if item.is_active else None
    item.save(update_fields=["submission_position"])
    Submission.objects.create(work_item=item, idempotency_key=key, channel="staff",
                              owner_email=item.contact_email.strip().lower(),
                              answers={name: audit_value(value) for name, value in data.items()})
    AuditEvent.objects.create(work_item=item, actor=actor, action="created",
                              changes={"reference": item.reference, "project": audit_value(item.project_id)})
    state.save(update_fields=["last_reference_number", "last_queue_order"])
    return item, True


EDIT_FIELDS = {"status", "priority", "queue_order", "estimated_days", "requested_deadline",
               "committed_due_date", "scheduled_start", "estimated_completion", "followup_date",
               "internal_notes", "project"}


@transaction.atomic
def edit_work(*, item_id, version, data, actor, notify_client=False):
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    if item.version != version:
        raise EditConflict("This request changed in another tab. Review the current values before saving again.")
    if set(data) - EDIT_FIELDS:
        raise ValidationError("Unsupported queue field.")
    old_project_id = item.project_id
    before_status = item.status
    item.milestone_notification = None
    changes = {}
    for name, value in data.items():
        old = getattr(item, name)
        if old != value:
            changes[name] = {"before": audit_value(old.pk if name == "project" and old else old),
                             "after": audit_value(value.pk if name == "project" and value else value)}
            setattr(item, name, value)
    # Keep both ends of a prior-request relationship within the same project.
    if item.previous_request_id and item.previous_request.project_id != item.project_id:
        changes["previous_request"] = {"before": str(item.previous_request_id), "after": None}
        item.previous_request = None
    mismatched = list(item.updates.exclude(project_id=item.project_id))
    for update in mismatched:
        update.previous_request = None
        update.version += 1
        update.save(update_fields=["previous_request", "version", "updated_at"])
        AuditEvent.objects.create(work_item=update, actor=actor, action="prior_link_cleared",
                                  changes={"previous_request": {"before": str(item.pk), "after": None}})
    # The Sheet import intentionally retained missing intake details. Queue edits
    # cannot change these fields, so preserve historical blanks while validating
    # present values and every queue constraint. New work still uses full_clean().
    missing_intake_fields = {name for name in (
        "contact_full_name", "company", "contact_email", "contact_phone", "description"
    ) if not getattr(item, name)}
    item.full_clean(exclude=missing_intake_fields)
    if changes:
        item.version += 1
        item.save()
        if old_project_id != item.project_id:
            if old_project_id:
                old_project = WorkProject.objects.get(pk=old_project_id)
                if old_project.canonical_reference == item.reference:
                    remaining = old_project.work_items.order_by("received_at", "id").first()
                    old_project.canonical_reference = remaining.reference if remaining else None
                    old_project.save(update_fields=["canonical_reference"])
            if item.project_id:
                project = WorkProject.objects.get(pk=item.project_id)
                if not project.canonical_reference:
                    project.canonical_reference = item.reference
                    project.save(update_fields=["canonical_reference"])
        AuditEvent.objects.create(work_item=item, actor=actor, action="edited", changes=changes)
        from .milestones import record_milestone
        item.milestone_notification = record_milestone(item=item, before_status=before_status,
            notify_client=notify_client, actor=actor)
        if item.milestone_notification:
            AuditEvent.objects.create(work_item=item, actor=actor, action="milestone_email_decided",
                changes={"milestone": item.milestone_notification.pk, "status": item.status,
                         "email": "queued" if item.milestone_notification.delivery_id else "skipped"})
    return item


@transaction.atomic
def change_project_access(*, item_id, version, email, allow, actor):
    from django.core.validators import validate_email
    from django.utils import timezone
    from .access import normalize_email
    from .models import ProjectAccess
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    if item.version != version:
        raise EditConflict("This request changed. Reload it before changing project access.")
    if not item.project_id:
        raise ValidationError("Connect the request to a project before managing access.")
    email = normalize_email(email)
    validate_email(email)
    grant, _ = ProjectAccess.objects.get_or_create(project=item.project, email=email)
    grant.revoked_at = None if allow else timezone.now()
    grant.granted_by = actor
    grant.save(update_fields=["revoked_at", "granted_by"])
    item.version += 1
    item.save(update_fields=["version", "updated_at"])
    AuditEvent.objects.create(work_item=item, actor=actor, action="access_granted" if allow else "access_revoked",
                              changes={"email": email, "project": str(item.project_id)})
    return item
