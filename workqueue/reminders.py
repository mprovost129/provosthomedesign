"""One opt-in reminder per information request, rechecked before actual delivery."""
import uuid
from django.db import transaction
from django.utils import timezone
from .access import authorized_work, normalize_email
from .information_requests import information_email
from .models import InformationRequest, NotificationDelivery, Status
from .services import lock_queue


def still_waiting(note):
    item = note.work_item
    return (item.status == Status.NEEDS_INFORMATION and normalize_email(item.contact_email) == normalize_email(note.delivery.recipient)
        and not note.responses.exists() and not note.reminder_canceled_at
        and not item.information_requests.filter(created_at__gt=note.created_at).exists()
        and not item.information_requests.filter(created_at=note.created_at, pk__gt=note.pk).exists()
        and authorized_work(note.delivery.recipient).filter(pk=item.pk).exists())


def queue_due_reminders(limit=100):
    # A failed original question must not occupy the limited batch and prevent
    # other clients' eligible reminders from ever being considered.
    ids = InformationRequest.objects.filter(reminder_date__lte=timezone.localdate(), delivery__state='sent',
        reminder_delivery__isnull=True, reminder_canceled_at__isnull=True).order_by('reminder_date', 'pk').values_list('pk', flat=True)[:limit]
    processed = 0
    for note_id in list(ids):
        with transaction.atomic():
            lock_queue()
            note = InformationRequest.objects.select_for_update().get(pk=note_id)
            if note.reminder_delivery_id or note.reminder_canceled_at:
                continue
            if not still_waiting(note):
                note.reminder_canceled_at = timezone.now()
                note.save(update_fields=['reminder_canceled_at'])
                continue
            if note.delivery.state != 'sent':
                continue
            email = information_email(note.work_item, note.message)
            email['subject'] = f"Reminder — information needed: {note.work_item.reference}"
            email['body'] = email['body'].replace('I need some additional information', 'A reminder: I am still waiting for additional information', 1)
            note.reminder_delivery = NotificationDelivery.objects.create(recipient_kind='client', **email,
                provider_reference=f"queue-{uuid.uuid4().hex}")
            note.save(update_fields=['reminder_delivery'])
            processed += 1
    return processed


def cancel_ineligible_reminder(record):
    note = InformationRequest.objects.filter(reminder_delivery=record).first()
    if note and not still_waiting(note):
        note.reminder_canceled_at = timezone.now()
        note.save(update_fields=['reminder_canceled_at'])
        record.state, record.body = 'canceled', ''
        record.save(update_fields=['state', 'body'])
        return True
    return False
