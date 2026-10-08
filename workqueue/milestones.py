"""Allowlisted client messages, written atomically with a real status transition."""
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.urls import reverse

from .models import NotificationDelivery, Status, StatusMilestone
from .notifications import site_url


MILESTONE_TEXT = {
    Status.IN_PROGRESS: ("Work has started", "Work has started on your request."),
    Status.COMPLETED: ("Work is complete", "The work for your request has been marked complete. If you have questions about your deliverables, please contact us."),
}


def record_milestone(*, item, before_status, notify_client, actor):
    # Called inside edit_work's queue lock and transaction. Never serialize internal fields.
    if before_status == item.status or item.status not in MILESTONE_TEXT:
        return None
    delivery = None
    if notify_client:
        if not getattr(settings, "WORK_INTAKE_ENABLED", False):
            raise ValidationError("Client emails are unavailable while client intake is disabled. Choose Skip email to save.")
        validate_email(item.contact_email)
        title, message = MILESTONE_TEXT[item.status]
        tracking_url = site_url(reverse("workqueue:tracking")) + "?" + urlencode({"reference": item.reference})
        project = item.project.name if item.project_id else item.project_name
        delivery = NotificationDelivery.objects.create(recipient_kind="client", recipient=item.contact_email,
            subject=f"{title}: {item.reference}",
            body=(f"Hello {item.contact_full_name},\n\n{message}\n\nRequest ID: {item.reference}\n"
                  + (f"Project: {project}\n" if project else "")
                  + f"Status: {item.get_status_display()}\n\nView your request:\n{tracking_url}\n\n"
                    "Use your submitting email address to sign in.\n\nThank you,\nProvost Home Design\n"),
            provider_reference=f"queue-{uuid.uuid4().hex}")
    return StatusMilestone.objects.create(work_item=item, actor=actor, before_status=before_status,
        status=item.status, item_version=item.version, delivery=delivery)
