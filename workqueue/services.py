"""All queue writes use one lock order: singleton queue state, then work item.

PostgreSQL row locks protect allocation, receipt snapshots and concurrent edits.
SQLite is suitable for the isolated preview, not evidence of production locking.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from .models import AuditEvent, CLOSED_STATUSES, QueueState, Submission, WorkItem, WorkProject


class EditConflict(Exception):
    pass


def lock_queue():
    # Seeded by migration so the first two concurrent submissions cannot race to create it.
    return QueueState.objects.select_for_update().get(pk=1)


def audit_value(value):
    return str(value) if value is not None else None


@transaction.atomic
def create_work(*, data, actor, idempotency_key=None, staff_contact=None, portal_member=None):
    state = lock_queue()
    if staff_contact is not None:
        from .models import ClientContact
        from django.core.exceptions import PermissionDenied
        if not (actor and actor.is_active and actor.is_staff and actor.has_perms(
                ("workqueue.view_workitem", "workqueue.add_workitem"))):
            raise PermissionDenied
        staff_contact = ClientContact.objects.select_related("client").get(pk=staff_contact.pk)
        if staff_contact.client.merged_into_id or staff_contact.archived_at:
            raise ValidationError("Choose an active contact on the current client record.")
    if portal_member is not None:
        from .portal_models import PortalMember
        from .access import normalize_email
        from django.core.exceptions import PermissionDenied
        if staff_contact is not None or actor is not None:
            raise PermissionDenied
        portal_member = PortalMember.objects.select_related('contact', 'client').get(pk=portal_member.pk)
        if (portal_member.revoked_at or not portal_member.review_access or portal_member.client.merged_into_id
                or portal_member.contact.archived_at or portal_member.contact.client_id != portal_member.client_id
                or normalize_email(portal_member.contact.email or '') != portal_member.email
                or data.get('project') is None or data['project'].pk != portal_member.project_id
                or data.get('kind') != 'update' or normalize_email(data.get('contact_email', '')) != portal_member.email):
            raise PermissionDenied
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
    # Only authenticated staff recording work for an explicit CRM contact may
    # retain unknown email/phone. Public intake retains its full validation.
    missing_contact = {name for name in ("contact_email", "contact_phone")
                       if (staff_contact is not None or portal_member is not None) and not getattr(item, name)}
    item.full_clean(exclude=missing_contact)
    if item.kind == WorkItem.Kind.NEW and project is None:
        project = WorkProject.objects.create(
            name=item.project_name or item.project_street or item.project_context or item.reference,
            street=item.project_street, city=item.project_city, state=item.project_state,
            zip_code=item.project_zip, canonical_reference=item.reference,
        )
        item.project = project
    if staff_contact is not None or portal_member is not None:
        item.client_contact = staff_contact or portal_member.contact
    item.save()
    if portal_member is not None:
        pass  # Exact approved portal contact; never fuzzy-match or expand CRM access.
    elif staff_contact is None:
        from .crm import attach_client
        attach_client(item)
    else:
        from .crm import changed
        changed(staff_contact.client, actor, "Request added manually", {"request": item.reference})
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
               "internal_notes", "project", "project_name", "project_street", "project_city",
               "project_state", "project_zip", "service_needed", "description"}

PROJECT_FIELDS = {'project_name': 'name', 'project_street': 'street', 'project_city': 'city',
                  'project_state': 'state', 'project_zip': 'zip_code'}


@transaction.atomic
def edit_work(*, item_id, version, data, actor, notify_client=False):
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    if item.version != version:
        raise EditConflict("This request changed in another tab. Review the current values before saving again.")
    if set(data) - EDIT_FIELDS:
        raise ValidationError("Unsupported queue field.")
    old_project_id = item.project_id
    old_project = item.project
    data = dict(data)
    target_project = data.get('project', old_project)
    project_changes = {}
    if target_project:
        target_project = WorkProject.objects.select_for_update().get(pk=target_project.pk)
        for name, attribute in PROJECT_FIELDS.items():
            if name not in data:
                continue
            value = data[name]
            initial = getattr(old_project, attribute) if old_project else getattr(item, name)
            if target_project.pk != old_project_id and value == initial:
                data[name] = getattr(target_project, attribute)
                continue
            if value != getattr(target_project, attribute):
                project_changes[attribute] = {'before': getattr(target_project, attribute), 'after': value}
                setattr(target_project, attribute, value)
        if project_changes:
            target_project.full_clean()
            target_project.save(update_fields=list(project_changes))
            # Shared project edits invalidate every linked row's previously opened form.
            for sibling in WorkItem.objects.select_for_update().filter(project=target_project).exclude(pk=item.pk):
                sibling.version += 1
                sibling.save(update_fields=['version', 'updated_at'])
                AuditEvent.objects.create(work_item=sibling, actor=actor, action='project_details_edited',
                                          changes=project_changes)
        if 'project' in data:
            data['project'] = target_project
    before_status = item.status
    item.milestone_notification = None
    changes = {}
    if project_changes:
        changes['project_details'] = project_changes
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


def require_queue_staff(actor, permission):
    from django.core.exceptions import PermissionDenied
    if not (actor and actor.is_active and actor.is_staff and actor.has_perms(
            ('workqueue.view_workitem', permission))):
        raise PermissionDenied


@transaction.atomic
def copy_work(*, item_id, version, data, token, actor):
    require_queue_staff(actor, 'workqueue.add_workitem')
    lock_queue()
    source = WorkItem.objects.select_for_update(of=('self',)).select_related('client_contact', 'project').get(pk=item_id)
    key = f'copy:{actor.pk}:{source.pk}:{uuid.UUID(str(token))}'
    saved = Submission.objects.select_related('work_item').filter(idempotency_key=key).first()
    if saved:
        return saved.work_item, False
    if source.version != version:
        raise EditConflict('The original request changed. Reload Copy and review it before saving.')
    fields = {'kind', 'project', 'project_context', 'project_name', 'project_street', 'project_city',
              'project_state', 'project_zip', 'service_needed', 'description', 'requested_deadline'}
    if set(data) - fields:
        raise ValidationError('Unsupported copied field.')
    inherited = {name: getattr(source, name) for name in (
        'contact_full_name', 'company', 'contact_email', 'contact_phone',
        'billing_street', 'billing_city', 'billing_state', 'billing_zip')}
    inherited.update(data)
    inherited['source'] = 'Staff copy'
    # Historical blanks are allowed only for authenticated staff using a CRM contact.
    item, created = create_work(data=inherited, actor=actor, idempotency_key=key,
                               staff_contact=source.client_contact)
    if created:
        AuditEvent.objects.create(work_item=item, actor=actor, action='copied',
                                  changes={'original_request': source.reference})
    return item, created


@transaction.atomic
def delete_work(*, item_id, version, actor):
    from django.utils import timezone
    from django.db.models import Q
    from .models import InformationRequest, NotificationDelivery, StatusMilestone, CompletedDelivery
    require_queue_staff(actor, 'workqueue.delete_workitem')
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    if item.version != version:
        raise EditConflict('This request changed. Review the current values before deleting it.')
    item.deleted_at = timezone.now()
    item.version += 1
    item.save(update_fields=['deleted_at', 'version', 'updated_at'])
    # Cancel unsent messages; sending/unknown outcomes are retained for reconciliation.
    notes = InformationRequest.objects.filter(work_item=item)
    notice_ids = list(notes.values_list('delivery_id', flat=True)) + list(notes.values_list('reminder_delivery_id', flat=True))
    notice_ids += list(StatusMilestone.objects.filter(work_item=item).values_list('delivery_id', flat=True))
    notice_ids += list(CompletedDelivery.objects.filter(work_item=item).values_list('notification_id', flat=True))
    NotificationDelivery.objects.filter(Q(submission__work_item=item) | Q(pk__in=notice_ids),
                                        state='pending').update(state='canceled', body='')
    notes.filter(reminder_canceled_at__isnull=True).update(reminder_canceled_at=item.deleted_at)
    if item.project_id and item.project.canonical_reference == item.reference:
        remaining = WorkItem.objects.filter(project_id=item.project_id).first()
        item.project.canonical_reference = remaining.reference if remaining else None
        item.project.save(update_fields=['canonical_reference'])
    AuditEvent.objects.create(work_item=item, actor=actor, action='deleted',
                              changes={'deleted_at': item.deleted_at.isoformat()})
    return item


@transaction.atomic
def restore_work(*, item_id, version, actor):
    require_queue_staff(actor, 'workqueue.delete_workitem')
    require_queue_staff(actor, 'workqueue.change_workitem')
    state = lock_queue()
    item = WorkItem.all_objects.select_for_update().get(pk=item_id, deleted_at__isnull=False)
    if item.version != version:
        raise EditConflict('This deleted request changed. Reload before restoring it.')
    # Restored active work appends so it cannot silently jump ahead of current clients.
    if item.status not in CLOSED_STATUSES:
        maximum = WorkItem.all_objects.aggregate(value=Max('queue_order'))['value'] or 0
        state.last_queue_order = max(state.last_queue_order, maximum) + 1
        item.queue_order = state.last_queue_order
        state.save(update_fields=['last_queue_order'])
    item.deleted_at = None
    item.version += 1
    item.save(update_fields=['deleted_at', 'version', 'queue_order', 'updated_at'])
    if item.project_id and not item.project.canonical_reference:
        item.project.canonical_reference = item.reference
        item.project.save(update_fields=['canonical_reference'])
    AuditEvent.objects.create(work_item=item, actor=actor, action='restored',
                              changes={'queue_order': item.queue_order, 'emails': 'Canceled messages remain canceled'})
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
