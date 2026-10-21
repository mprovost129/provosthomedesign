"""A committed outbox survives request failures; ambiguous sends are held for review."""
import logging
import hashlib
import re
import smtplib
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import BadHeaderError, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token
from .models import EmailAccessToken, NotificationDelivery
from .receipts import submission_record

logger = logging.getLogger(__name__)


def site_url(path):
    base = getattr(settings, "INTAKE_PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        raise ImproperlyConfigured("INTAKE_PUBLIC_BASE_URL is required for client emails.")
    return base + path


def access_url(raw):
    # Fragments are not sent to the server, reverse proxy or request access logs.
    return site_url(reverse("workqueue:confirm_access")) + "#" + urlencode({"token": raw})


def queue_receipts(submission):
    item = submission.work_item
    record, raw = mint_email_token(item.contact_email, item.reference)
    count = item.submission_position
    body = (f"Hello {item.contact_full_name},\n\nWe received your {item.get_kind_display().lower()}.\n"
            f"Request ID: {item.reference}\nPosition when submitted: {count}\nActive requests ahead: {count - 1}\n\n"
            f"Track your current position and status:\n{access_url(raw)}\n\n"
            "This sign-in link expires in 24 hours. Request another link from Track My Project:\n"
            f"{site_url(reverse('workqueue:tracking'))}\n\n"
            "Your position can change and does not guarantee a wait time or project start date.\n"
            "We will contact you if more information is needed.\n")
    if item.project_id:
        body += f"Connected project: {item.project.name} ({item.project.canonical_reference or item.reference})\n"
    body += "\n" + submission_record(submission, site_url) + "\n\nProvost Home Design\n"
    NotificationDelivery.objects.get_or_create(submission=submission, recipient_kind="client", defaults={
        "recipient": item.contact_email, "subject": f"We received request {item.reference}", "body": body,
        "provider_reference": f"queue-{uuid.uuid4().hex}"})
    answers = "\n\n".join(f"{question}: {', '.join(map(str, answer)) if isinstance(answer, list) else answer}"
                            for question, answer in submission.answers.items() if answer not in (None, "", []))
    files = "\n".join(f"{file.original_name}: {site_url(reverse('workqueue:download', args=[file.pk]))}"
                      for file in submission.attachments.all()) or "No files uploaded."
    owner_body = (f"New {item.get_kind_display().lower()} — {item.reference}\nPosition at submission: {count}\n\n"
                  f"{'Connected project: ' + str(item.project) if item.project_id else 'Project link needs confirmation'}\n\n"
                  f"{answers}\n\nFiles:\n{files}\n\nManage this request:\n"
                  f"{site_url(reverse('workqueue:queue'))}#request-{item.pk}\n")
    NotificationDelivery.objects.get_or_create(submission=submission, recipient_kind="owner", defaults={
        "recipient": settings.INTAKE_OWNER_EMAIL, "subject": f"New work received: {item.reference}",
        "body": owner_body, "provider_reference": f"queue-{uuid.uuid4().hex}"})


@transaction.atomic
def queue_signin(email, reference="", destination="tracking"):
    record, raw = mint_email_token(email, reference, destination)
    return NotificationDelivery.objects.create(recipient_kind="signin", recipient=record.email,
        subject="Your Provost Home Design sign-in link",
        body=f"Continue to your projects:\n{access_url(raw)}\n\nThis link expires in 24 hours and can be used once.\n"
             "If you did not request it, you can ignore this email.\n", provider_reference=f"queue-{uuid.uuid4().hex}")


def deliver_pending(limit=100):
    from .reminders import queue_due_reminders, cancel_ineligible_reminder
    from .operations import queue_error_alerts
    queue_error_alerts()
    queue_due_reminders(limit)
    from .portal_notices import queue_review_reminders, cancel_ineligible_portal_notice
    queue_review_reminders(limit)
    from .crm_mail import queue_morning_digest
    queue_morning_digest()
    from .portal_discussions import cancel_ineligible_reply
    from .calendar_reconciliation import cancel_obsolete_appointment_notice
    # A worker that crashed after handing a message to the provider may have sent it.
    # Hold stale sending records instead of automatically sending a second copy.
    NotificationDelivery.objects.filter(state="sending", last_attempt_at__lt=timezone.now()-timedelta(minutes=15)).update(
        state="unknown", last_error="Delivery was interrupted; check provider logs before resending.")
    processed = 0
    for _ in range(limit):
        with transaction.atomic():
            record = NotificationDelivery.objects.select_for_update().filter(state="pending").order_by("created_at", "pk").first()
            if record is None:
                break
            deleted = NotificationDelivery.objects.filter(pk=record.pk).filter(
                Q(submission__work_item__deleted_at__isnull=False)
                | Q(information_request__work_item__deleted_at__isnull=False)
                | Q(information_reminder__work_item__deleted_at__isnull=False)
                | Q(status_milestone__work_item__deleted_at__isnull=False)
                | Q(completed_delivery__work_item__deleted_at__isnull=False)).exists()
            if deleted:
                record.state, record.body = 'canceled', ''
                record.save(update_fields=['state', 'body'])
                continue
            if cancel_obsolete_appointment_notice(record):
                continue
            if cancel_ineligible_portal_notice(record):
                continue
            if cancel_ineligible_reply(record):
                continue
            if cancel_ineligible_reminder(record):
                continue
            if hasattr(record, 'morning_digest'):
                from .models import CRMSettings
                if not CRMSettings.objects.filter(pk=1, morning_reminders=True).exists():
                    record.state, record.body = 'canceled', ''
                    record.save(update_fields=['state', 'body'])
                    continue
            if record.provider_reference.startswith('operationserror-'):
                from .operations_models import OperationsSettings
                if OperationsSettings.objects.filter(pk=1, email_alerts=False).exists():
                    record.state, record.body = 'canceled', ''
                    record.save(update_fields=['state', 'body'])
                    continue
            # Long-delayed or explicitly retried mail must contain a usable sign-in link.
            if record.recipient_kind in {"client", "signin"}:
                match = re.search(r"#token=([A-Za-z0-9_-]+)", record.body)
                if match:
                    old = EmailAccessToken.objects.filter(digest=hashlib.sha256(match[1].encode()).hexdigest()).first()
                    if old and (old.used_at or old.expires_at < timezone.now()+timedelta(minutes=5)):
                        _, raw = mint_email_token(record.recipient, old.reference, old.destination)
                        record.body = record.body.replace("#token=" + match[1], "#token=" + raw)
            record.state = "sending"
            record.attempts += 1
            record.last_attempt_at = timezone.now()
            record.save(update_fields=["state", "attempts", "last_attempt_at", "body"])
        try:
            message = EmailMultiAlternatives(record.subject, record.body, settings.DEFAULT_FROM_EMAIL,
                record.to_recipients or [record.recipient], cc=record.cc_recipients, bcc=record.bcc_recipients,
                headers={"Message-ID": f"<{record.provider_reference}@provosthomedesign.com>"})
            if record.html_body:
                message.attach_alternative(record.html_body, 'text/html')
            result = message.send(fail_silently=False)
        except (BadHeaderError, smtplib.SMTPRecipientsRefused):
            state, error = "failed", "The message was rejected before delivery. Check the recipient and mail setup."
        except Exception:
            # Logs omit recipients, tokens, payloads, and potentially sensitive provider errors.
            logger.warning("Queue email %s has an unknown delivery outcome", record.pk)
            state, error = "unknown", "Check the mail provider before resending; delivery may have succeeded."
        else:
            state, error = ("sent", "") if result else ("failed", "The mail backend accepted no messages.")
        with transaction.atomic():
            current = NotificationDelivery.objects.select_for_update().get(pk=record.pk)
            if current.state == "sending":
                current.state = state
                current.last_error = error
                if state == "sent":
                    current.sent_at = timezone.now()
                    # Do not retain bearer sign-in links in the outbox after successful delivery.
                    current.body = ""
                    current.html_body = ""
                current.save(update_fields=["state", "last_error", "sent_at", "body", "html_body"])
        processed += 1
    return processed
