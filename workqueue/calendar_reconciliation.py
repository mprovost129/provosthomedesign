"""Adopt only verified changes to this app's own Google appointment events."""
import uuid
from datetime import UTC, timedelta
from django.db import transaction
from django.utils import timezone
from .calendar_provider import CalendarMismatch, CalendarUnavailable, EASTERN, expected_events, moment
from .models import Appointment, BookingAudit


class LostClaim(CalendarUnavailable):
    pass


def owned_times(item, event):
    if (event.get('id') != item.event_ids[0]
            or event.get('extendedProperties', {}).get('private', {}).get('phdAppointment') != str(item.pk)
            or not event.get('etag') or event.get('recurrence') or event.get('recurringEventId')):
        raise CalendarMismatch('Calendar appointment identity changed; please review this meeting.')
    try:
        if 'dateTime' not in event.get('start', {}) or 'dateTime' not in event.get('end', {}):
            raise ValueError
        start, end = moment(event['start']).astimezone(UTC), moment(event['end']).astimezone(UTC)
        duration = (end-start).total_seconds()
        if not 60 <= duration <= 8*3600 or duration % 60:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise CalendarMismatch('Use a single timed meeting of 1 minute to 8 hours. All-day or repeating meetings need review.') from None
    return start, end


def primary_unchanged(item, first, current):
    if current is None or current.get('status') == 'cancelled' or current.get('etag') != first.get('etag'):
        raise CalendarUnavailable('The meeting changed again during sync. It will be checked on the next run.')
    if owned_times(item, current) != owned_times(item, first):
        raise CalendarUnavailable('The meeting time changed again during sync. It will be checked on the next run.')


@transaction.atomic
def adopt_times(item, start, end, *, restored=False):
    from .booking import lock_bookings
    lock_bookings()
    current = Appointment.objects.select_for_update().get(pk=item.pk)
    if current.sync_token != item.sync_token:
        raise LostClaim
    if start != current.starts_at or end != current.ends_at or restored:
        old = {'start':current.starts_at.isoformat(), 'end':current.ends_at.isoformat()}
        current.starts_at, current.ends_at = start, end
        current.reserved_until = end+timedelta(minutes=30)
        current.local_date = start.astimezone(EASTERN).date()
        current.calendar_change_number += 1
        current.calendar_change_kind = 'restored' if restored else 'changed'
        if restored:
            current.cancel_requested = False
            current.state = 'syncing'
        current.save(update_fields=['starts_at','ends_at','reserved_until','local_date',
            'calendar_change_number','calendar_change_kind','cancel_requested','state'])
        BookingAudit.objects.create(appointment=current, action='google_times_observed', details={
            'previous':old, 'start':start.isoformat(), 'end':end.isoformat(), 'restored':restored})
    for field in ['starts_at','ends_at','reserved_until','local_date','calendar_change_number',
                  'calendar_change_kind','cancel_requested']:
        setattr(item, field, getattr(current, field))


@transaction.atomic
def replacement_buffer(item):
    from .booking import lock_bookings
    lock_bookings()
    current = Appointment.objects.select_for_update().get(pk=item.pk)
    if current.sync_token != item.sync_token:
        raise LostClaim
    if not current.buffer_pending_insert:
        # Deleted Google IDs can remain tombstones. Persist the replacement ID
        # before writing so a timeout/retry cannot create another buffer copy.
        current.buffer_event_id = 'phd'+uuid.uuid4().hex+'b'
        current.buffer_pending_insert = True
        current.save(update_fields=['buffer_event_id','buffer_pending_insert'])
    item.buffer_event_id, item.buffer_pending_insert = current.buffer_event_id, current.buffer_pending_insert


def reconcile_confirmed(item, provider, *, previously_closed=False):
    actual = provider.get(item.calendar_id, item.event_ids[0])
    if actual is None or actual.get('status') == 'cancelled':
        # Distinguish a deleted meeting from a lost/unknown calendar connection.
        provider.identity(item.calendar_id, refresh=True)
        provider.remove_buffer(item)
        latest = provider.get(item.calendar_id, item.event_ids[0])
        if latest and latest.get('status') != 'cancelled':
            raise CalendarUnavailable('The meeting was restored during sync. It will be checked on the next run.')
        return 'canceled', True
    start, end = owned_times(item, actual)
    adopt_times(item, start, end, restored=previously_closed and end+timedelta(minutes=30) > timezone.now())
    if item.reserved_until <= timezone.now():
        primary_unchanged(item, actual, provider.get(item.calendar_id, item.event_ids[0]))
        return 'completed', False
    buffer = provider.get(item.calendar_id, item.event_ids[1])
    if buffer is None or buffer.get('status') == 'cancelled':
        replacement_buffer(item)
    provider.reconcile_buffer(item, expected_events(item)[1])
    primary_unchanged(item, actual, provider.get(item.calendar_id, item.event_ids[0]))
    from .booking import overlaps
    if overlaps(item.starts_at, item.reserved_until,
            provider.busy(item.starts_at, item.reserved_until, exclude=item.event_ids, calendar_id=item.calendar_id)):
        raise CalendarUnavailable('Google moved this appointment into another busy period. Its new time is reserved; please review the conflict.')
    return 'confirmed', False


def original_deleted(previous_id):
    return bool(previous_id and Appointment.objects.filter(pk=previous_id, state='canceled').exists()
        and BookingAudit.objects.filter(appointment_id=previous_id, action='google_canceled').exists())


def cancel_obsolete_appointment_notice(notice):
    if not notice.appointment_id:
        return False
    item = Appointment.objects.get(pk=notice.appointment_id)
    event = notice.appointment_event
    if event == 'canceled' or event.startswith('canceled-'):
        generation = event.split('-', 1)[1] if '-' in event else '0'
        allowed = item.state == 'canceled' and generation == str(item.calendar_change_number)
    elif event == 'confirmed':
        allowed = item.state in {'confirmed','attention'} and not item.cancel_requested and not item.calendar_change_number
    elif event.startswith(('changed-', 'restored-')):
        allowed = (item.state in {'confirmed','attention'} and not item.cancel_requested and not item.calendar_change_kind
            and event.split('-', 1)[1] == str(item.calendar_change_number))
    else:
        return False
    if not allowed:
        notice.state, notice.body, notice.html_body = 'canceled', '', ''
        notice.save(update_fields=['state','body','html_body'])
    return not allowed
