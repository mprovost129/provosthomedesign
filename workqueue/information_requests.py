"""Explicitly reviewed client emails with durable, idempotent queue writes."""
import uuid
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse

from .models import AuditEvent, InformationRequest, NotificationDelivery, Status, WorkItem
from .notifications import site_url
from .services import EditConflict, lock_queue


INFORMATION_TEMPLATES = {
    "documents": "Please upload the current plans, survey, and any relevant photos so I can review your request. Let me know if any of these are not available.",
    "details": "Please provide a little more detail about the work you need, including the areas affected and the changes you would like made.",
    "corrections": "Please upload your marked-up plans or a written list of the corrections you would like me to review.",
}
TEMPLATE_CHOICES = [("documents", "Plans, survey or photos"), ("details", "Clarify the requested work"),
                    ("corrections", "Marked-up plans or corrections")]


def information_email(item, message):
    update_url = site_url(reverse("workqueue:submit")) + "?" + urlencode({"kind": "update", "reference": item.reference})
    tracking_url = site_url(reverse("workqueue:tracking")) + "?" + urlencode({"reference": item.reference})
    return {
        "recipient": item.contact_email,
        "subject": f"More information needed: {item.reference}",
        "body": (f"Hello {item.contact_full_name},\n\n"
                 f"I need some additional information for request {item.reference}.\n\n{message.strip()}\n\n"
                 f"Send your information or files here:\n{update_url}\n\n"
                 "Use the same email address as your original request. Your request ID is already filled in. "
                 "Updates receive their own place in the queue and are connected to your project after email verification.\n\n"
                 f"Check your current status:\n{tracking_url}\n\nThank you,\nProvost Home Design\n"),
    }


@transaction.atomic
def request_information(*, item_id, version, message, followup_date, token, actor, reminder_date=None):
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    existing = InformationRequest.objects.filter(token=token).select_related("delivery").first()
    if existing:
        if (existing.work_item_id != item.pk or existing.actor_id != actor.pk
                or existing.message != message.strip() or existing.followup_date != followup_date or existing.reminder_date != reminder_date):
            raise ValidationError("This email confirmation was already used. Reload the request before composing another email.")
        return item, False
    if item.version != version:
        raise EditConflict("This request changed in another tab. Review the current request and preview the email again.")
    if not item.is_active:
        raise ValidationError("Reopen this request before requesting more information.")
    message = message.strip()
    if not message or len(message) > 5000:
        raise ValidationError("Enter a client message of up to 5,000 characters.")
    from django.utils import timezone
    if reminder_date and reminder_date <= timezone.localdate():
        raise ValidationError('Choose a future date for the client reminder.')
    email = information_email(item, message)
    delivery = NotificationDelivery.objects.create(recipient_kind="client", **email,
        provider_reference=f"queue-{uuid.uuid4().hex}")
    record = InformationRequest.objects.create(work_item=item, actor=actor, message=message,
        followup_date=followup_date, token=token, delivery=delivery, reminder_date=reminder_date)
    before = {"status": item.status, "followup_date": str(item.followup_date) if item.followup_date else None}
    item.status = Status.NEEDS_INFORMATION
    item.followup_date = followup_date
    item.version += 1
    item.save(update_fields=["status", "followup_date", "version", "updated_at"])
    AuditEvent.objects.create(work_item=item, actor=actor, action="information_requested",
        changes={"information_request": record.pk, "before": before,
                 "after": {"status": item.status, "followup_date": str(followup_date) if followup_date else None}})
    return item, True
