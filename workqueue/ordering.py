"""Reorder the complete active queue under the same lock as intake and edits."""
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, WorkItem
from .services import EditConflict, lock_queue

SALT = "workqueue.ordering.v1"
CONFLICT = "The queue changed while you were arranging it. Reload the order and review it before saving again."


def fingerprint(items):
    state = [(str(item.pk), item.version, item.queue_order) for item in items]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()


@transaction.atomic
def order_snapshot():
    lock_queue()
    items = list(WorkItem.objects.active().select_related("project"))
    return {"snapshot": signing.dumps(fingerprint(items), salt=SALT), "items": [
        {"id": str(item.pk), "reference": item.reference, "client": item.contact_full_name,
         "project": (item.project.name if item.project else "") or item.project_name
                    or item.project_context or item.project_street or "Untitled project",
         "kind": item.get_kind_display(), "status": item.get_status_display()}
        for item in items]}


@transaction.atomic
def save_order(*, ordered_ids, snapshot, actor):
    if not isinstance(ordered_ids, list) or any(not isinstance(value, str) for value in ordered_ids):
        raise ValidationError("Supply the complete active queue order.")
    try:
        ids = [uuid.UUID(value) for value in ordered_ids]
    except (ValueError, AttributeError):
        raise ValidationError("Invalid request in queue order.") from None
    if len(ids) != len(set(ids)):
        raise ValidationError("Each active request must appear exactly once.")
    try:
        expected = signing.loads(snapshot, salt=SALT, max_age=7200)
    except (signing.BadSignature, TypeError):
        raise EditConflict(CONFLICT) from None
    lock_queue()
    items = list(WorkItem.objects.active().select_for_update())
    if expected != fingerprint(items):
        raise EditConflict(CONFLICT)
    if set(ids) != {item.pk for item in items}:
        raise ValidationError("Include every active request exactly once. Closed work cannot be reordered.")
    if ids == [item.pk for item in items]:
        return False
    by_id = {item.pk: (position, item) for position, item in enumerate(items, 1)}
    changed, events = [], []
    now = timezone.now()
    for position, item_id in enumerate(ids, 1):
        before_position, item = by_id[item_id]
        if item.queue_order == position and before_position == position:
            continue
        events.append(AuditEvent(work_item=item, actor=actor, action="queue_reordered", changes={
            "queue_order": {"before": str(item.queue_order), "after": str(position)},
            "position": {"before": before_position, "after": position},
        }))
        item.queue_order = position
        item.version += 1
        item.updated_at = now
        changed.append(item)
    WorkItem.objects.bulk_update(changed, ["queue_order", "version", "updated_at"])
    AuditEvent.objects.bulk_create(events)
    # Keep allocator high-water marks and original receipt positions intact.
    # Future submissions still append; ordering never schedules client emails.
    return True
