"""Reservations hold capacity until both Google outcomes are known."""
import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .access import authorized_projects, mint_email_token, normalize_email
from .calendar_provider import EASTERN, CalendarUnavailable, calendar_provider, expected_events
from .models import Appointment, BookingAccess, BookingAudit, BookingState, ClientContact, NotificationDelivery
from .notifications import access_url, site_url


HOLD_STATES = ["pending", "syncing", "confirmed", "attention"]


def lock_bookings():
    return BookingState.objects.select_for_update().get(pk=1)


def eligible(email):
    """Verified-email callers may book through a grant or their current CRM contact.

    Explicit allow/block records take precedence. CRM matching is checked live,
    so an imported contact or corrected email works in an existing verified
    session without creating a permanent grant or granting project access.
    """
    if not email:
        return False
    email = normalize_email(email)
    override = BookingAccess.objects.filter(email=email).values_list('allowed', flat=True).first()
    if override is not None:
        return override
    return ClientContact.objects.filter(email__iexact=email, archived_at__isnull=True,
        client__merged_into__isnull=True).exists()


def overlaps(start, end, intervals):
    return any(start < other_end and end > other_start for other_start, other_end in intervals)


def candidate_times(day, now=None):
    now = now or timezone.now()
    if day.weekday() not in (1, 2, 3, 4):
        return []
    today = now.astimezone(EASTERN).date()
    if not today <= day <= today + timedelta(days=settings.BOOKING_HORIZON_DAYS):
        return []
    return [start for index in range(7)
            if (start := datetime.combine(day, time(10), EASTERN) + timedelta(minutes=30*index)) >= now+timedelta(hours=24)]


def active_appointments():
    return Appointment.objects.filter(state__in=HOLD_STATES)


def meeting_duration(previous=None):
    return timedelta(minutes=previous.duration_minutes if previous else 30)


def reservation_length(previous=None):
    return meeting_duration(previous)+timedelta(minutes=30)


def fits_booking_hours(start, previous=None):
    local = start.astimezone(EASTERN)
    return local+reservation_length(previous) <= datetime.combine(local.date(), time(14), EASTERN)


def valid_local_slot(start, email, *, previous=None, exclude=None, now=None):
    now = now or timezone.now()
    start = start.astimezone(EASTERN)
    if start not in candidate_times(start.date(), now):
        raise ValidationError("Choose an available Tuesday–Friday time at least 24 hours ahead.")
    if not fits_booking_hours(start, previous):
        raise ValidationError('Choose an earlier time so the full meeting and buffer end by 2 PM Eastern.')
    held = active_appointments()
    if exclude:
        held = held.exclude(pk=exclude)
    if previous:
        held = held.exclude(pk=previous.pk)
    if held.filter(email=email, reserved_until__gt=now).exists():
        raise ValidationError("You already have an upcoming appointment. Reschedule or cancel that appointment first.")
    if held.filter(local_date=start.date()).count() >= 2:
        raise ValidationError("That day already has two appointments. Please choose another day.")
    if held.filter(starts_at__lt=start+reservation_length(previous), reserved_until__gt=start).exists():
        raise ValidationError("That time has just been reserved. Please choose another time.")


def available_times(day, *, email, previous=None, provider=None):
    choices = candidate_times(day)
    if not choices:
        return []
    provider = provider or calendar_provider()
    exclude = previous.event_ids if previous else []
    busy = provider.busy(datetime.combine(day, time(0), EASTERN),
                         datetime.combine(day+timedelta(days=1), time(0), EASTERN), exclude=exclude)
    result = []
    for start in choices:
        if previous and start == previous.starts_at:
            continue
        try:
            valid_local_slot(start, email, previous=previous)
        except ValidationError:
            continue
        if not overlaps(start, start+reservation_length(previous), busy):
            result.append(start)
    return result


def available_days(first, last, *, email, previous=None, provider=None, now=None):
    """One bounded calendar read and one local reservation read for a month.

    Returns only bookable start times, never private calendar event details.
    The reservation path still validates the chosen time again under its lock.
    """
    if not 0 <= (last-first).days <= 30:
        raise ValueError('Availability must cover at most one calendar month.')
    now = now or timezone.now()
    candidates = {}
    for index in range((last-first).days+1):
        day = first+timedelta(days=index)
        choices = candidate_times(day, now)
        if choices:
            candidates[day] = choices
    if not candidates:
        return {}
    held = active_appointments()
    if previous:
        held = held.exclude(pk=previous.pk)
    records = list(held.filter(Q(local_date__range=(first,last)) |
                              Q(email=email, reserved_until__gt=now)).values(
                                  'email','local_date','starts_at','reserved_until'))
    if any(record['email'] == email and record['reserved_until'] > now for record in records):
        return {}
    provider = provider or calendar_provider()
    busy = provider.busy(datetime.combine(min(candidates),time(),EASTERN),
                         datetime.combine(max(candidates)+timedelta(days=1),time(),EASTERN),
                         exclude=previous.event_ids if previous else [])
    result = {}
    for day, choices in candidates.items():
        if sum(record['local_date'] == day for record in records) >= 2:
            continue
        slots = [start for start in choices if
                 (not previous or start != previous.starts_at) and
                 fits_booking_hours(start, previous) and
                 not overlaps(start,start+reservation_length(previous),busy) and
                 not any(start < record['reserved_until'] and start+reservation_length(previous) > record['starts_at']
                         for record in records)]
        if slots:
            result[day] = slots
    return result


def reserve_appointment(*, email, start, full_name, phone, purpose, key, project_id=None, previous_id=None, provider=None, terms_url="", meeting_method=None):
    email = normalize_email(email)
    if (not full_name.strip() or not phone.strip() or not purpose.strip()
            or len(purpose) > 3000 or len(full_name) > 200 or len(phone) > 50 or timezone.is_naive(start)):
        raise ValidationError("Provide your name, phone number and what you would like to discuss.")
    if meeting_method is not None and meeting_method not in Appointment.MeetingMethod.values:
        raise ValidationError("Choose how you would like to meet.")
    existing = Appointment.objects.filter(idempotency_key=key, email=email).first()
    if existing:
        return existing, False
    provider = provider or calendar_provider()
    # Read external availability before taking a database lock. The worker checks
    # it again before and after writing Google events, before any confirmation.
    previous = Appointment.objects.filter(pk=previous_id, email=email).first() if previous_id else None
    previous_snapshot = (previous.starts_at, previous.ends_at, tuple(previous.event_ids)) if previous else None
    calendar_id = provider.identity(settings.GOOGLE_CALENDAR_ID)["id"]
    if previous and previous.calendar_id != calendar_id:
        raise ValidationError("The appointment calendar changed. Please contact Mike before rescheduling.")
    busy = provider.busy(start, start+reservation_length(previous), exclude=previous.event_ids if previous else [], calendar_id=calendar_id)
    with transaction.atomic():
        lock_bookings()
        existing = Appointment.objects.filter(idempotency_key=key).first()
        if existing:
            if existing.email != email:
                raise ValidationError("Reload the booking form and try again.")
            return existing, False
        if not eligible(email):
            raise ValidationError("Booking access is not currently available. Please contact us.")
        if previous_id:
            previous = Appointment.objects.select_for_update().filter(pk=previous_id, email=email,
                state="confirmed", cancel_requested=False, starts_at__gt=timezone.now()).first()
            if not previous or Appointment.objects.filter(previous=previous, state__in=HOLD_STATES).exists():
                raise ValidationError("This appointment cannot be rescheduled. Review your upcoming appointment.")
            if previous_snapshot != (previous.starts_at, previous.ends_at, tuple(previous.event_ids)):
                raise ValidationError('The appointment changed. Reload and review its current time before rescheduling.')
            if start == previous.starts_at:
                raise ValidationError("Choose a different time to reschedule this appointment.")
        project = None
        if project_id:
            project = authorized_projects(email).filter(pk=project_id).first()
            if not project:
                raise ValidationError("Choose one of your connected projects.")
        valid_local_slot(start, email, previous=previous)
        if overlaps(start, start+reservation_length(previous), busy):
            raise ValidationError("Your calendar time is no longer available. Please choose another.")
        method = meeting_method if meeting_method is not None else (previous.meeting_method if previous else '')
        address = (getattr(settings, "CONTACT_ADDRESS", "7 Park St. Unit 1, Rehoboth, MA 02769").strip()
            if method == Appointment.MeetingMethod.IN_PERSON else '')
        if method == Appointment.MeetingMethod.IN_PERSON and (not address or 'Your City' in address):
            raise ValidationError("Please contact Mike to confirm the office address before booking in person.")
        item = Appointment.objects.create(meeting_method=method, meeting_address=address, email=email, full_name=full_name.strip(), phone=phone.strip(),
            purpose=purpose.strip(), project=project, previous=previous, starts_at=start,
            ends_at=start+meeting_duration(previous), reserved_until=start+reservation_length(previous),
            local_date=start.astimezone(EASTERN).date(), calendar_id=calendar_id, idempotency_key=key)
        consent = {"terms_url": terms_url, "terms_accepted_at": timezone.now().isoformat()} if terms_url else {}
        BookingAudit.objects.create(appointment=item, action="reschedule_requested" if previous else "booking_requested", details=consent)
        return item, True


@transaction.atomic
def request_cancel(appointment_id, *, email=None, actor=None):
    lock_bookings()
    item = Appointment.objects.select_for_update().get(pk=appointment_id)
    if email is not None and normalize_email(email) != item.email:
        raise ValidationError("This appointment is not available.")
    if item.state in ("canceled", "completed"):
        return item
    if item.starts_at <= timezone.now():
        raise ValidationError("Please contact us about an appointment that has already started.")
    if item.state == "syncing":
        raise ValidationError("The calendar is updating this appointment. Please try again shortly.")
    if Appointment.objects.filter(previous=item, state__in=HOLD_STATES).exists():
        raise ValidationError("Cancel your pending reschedule first, then cancel this appointment.")
    if not item.cancel_requested:
        item.cancel_requested = True
        item.last_sync_at = None
        item.save(update_fields=["cancel_requested", "last_sync_at"])
        BookingAudit.objects.create(appointment=item, actor=actor, action="cancellation_requested")
    return item


@transaction.atomic
def set_booking_access(email, allow, actor):
    lock_bookings()
    from django.core.validators import validate_email
    email = normalize_email(email)
    validate_email(email)
    access, _ = BookingAccess.objects.get_or_create(email=email)
    access.allowed = allow
    access.save()
    BookingAudit.objects.create(actor=actor, action="booking_access_granted" if allow else "booking_access_revoked", details={"email": email})


def appointment_notices(item, event):
    if NotificationDelivery.objects.filter(appointment=item, appointment_event=event).count() == 2:
        return
    action = ("canceled" if event == "canceled" or event.startswith('canceled-') else "restored" if event.startswith('restored-')
        else "updated" if event.startswith('changed-') else "rescheduled" if item.previous_id else "confirmed")
    start = item.starts_at.astimezone(EASTERN)
    display = start.strftime("%A, %B %d, %Y at %I:%M %p") + " Eastern"
    _, raw = mint_email_token(item.email, destination="booking")
    common = (f"Your appointment is {action}.\nAppointment: {item.reference}\nWhen: {display}\n"
              f"Meeting length: {item.duration_minutes} minutes\n"
              f"Meeting type: {item.get_meeting_method_display() or 'To be arranged'}\n{item.meeting_instructions}\n\nPurpose: {item.purpose}\n\n")
    client_body = common + f"Manage your appointment:\n{access_url(raw)}\n\nThis sign-in link expires in 24 hours and can be used once.\n"
    if action != "canceled":
        client_body += "This appointment does not change your project's place in line.\n"
    owner_body = common + f"Client: {item.full_name}\nEmail: {item.email}\nPhone: {item.phone}\n" + (
        f"Project: {item.project}\n" if item.project_id else "") + f"\nManage appointments:\n{site_url(reverse('workqueue:queue'))}#appointments\n"
    for kind, recipient, body in [("client", item.email, client_body), ("owner", settings.INTAKE_OWNER_EMAIL, owner_body)]:
        NotificationDelivery.objects.get_or_create(appointment=item, appointment_event=event, recipient_kind=kind,
            defaults={"recipient": recipient, "subject": f"Appointment {action}: {item.reference}", "body": body,
                      "provider_reference": "booking-" + uuid.uuid4().hex})


def sync_appointment(appointment_id, provider=None):
    provider = provider or calendar_provider()
    with transaction.atomic():
        lock_bookings()
        # Optional joins cannot be locked by PostgreSQL. The booking singleton
        # serializes related changes; lock only the appointment being processed.
        item = Appointment.objects.select_for_update(of=("self",)).select_related("previous", "project").get(pk=appointment_id)
        original_state = item.state
        previously_closed = original_state in {"canceled", "completed"}
        if previously_closed and (not item.confirmed_at or item.starts_at < timezone.now()-timedelta(days=7)):
            return False
        if item.state == "syncing" and item.sync_started_at and item.sync_started_at > timezone.now()-timedelta(minutes=15):
            return False
        if (previously_closed and item.sync_token and item.sync_started_at
                and item.sync_started_at > timezone.now()-timedelta(minutes=15)
                and (item.last_sync_at is None or item.last_sync_at < item.sync_started_at)):
            return False
        was_confirmed = item.confirmed_at is not None
        if not previously_closed:
            item.state = "syncing"
        else:
            # Watching a deleted/finished meeting must not hold its old slot.
            item.last_sync_at = None
        item.sync_started_at = timezone.now()
        item.sync_token = uuid.uuid4()
        item.save(update_fields=["state", "sync_started_at", "sync_token", "last_sync_at"])
    outcome, error, google_deleted = None, "", False
    meet_url = item.meet_url
    from .calendar_reconciliation import LostClaim, reconcile_confirmed, original_deleted
    try:
        if item.cancel_requested and not previously_closed:
            provider.remove(item)
            outcome = "canceled"
        elif was_confirmed:
            outcome, google_deleted = reconcile_confirmed(item, provider, previously_closed=previously_closed)
        elif original_deleted(item.previous_id):
            provider.remove(item)
            outcome = "canceled"
        else:
            if item.starts_at <= timezone.now():
                raise CalendarUnavailable("Booking time passed before confirmation; owner review required.")
            exclude = item.event_ids + (item.previous.event_ids if item.previous_id else [])
            busy = provider.busy(item.starts_at, item.reserved_until, exclude=exclude, calendar_id=item.calendar_id)
            if overlaps(item.starts_at, item.reserved_until, busy):
                raise CalendarUnavailable("Calendar conflict; choose another time or cancel this booking.")
            for expected in expected_events(item):
                provider.ensure(item, expected)
            busy = provider.busy(item.starts_at, item.reserved_until, exclude=exclude, calendar_id=item.calendar_id)
            if overlaps(item.starts_at, item.reserved_until, busy):
                raise CalendarUnavailable("Calendar conflict after save; owner review required.")
            if item.meeting_method == Appointment.MeetingMethod.GOOGLE_MEET:
                from .calendar_provider import verified_meet_url
                meet_url = verified_meet_url(provider.get(item.calendar_id, item.event_ids[0]), item)
            if item.previous_id:
                provider.remove(item.previous)
            outcome = "confirmed"
        if was_confirmed and outcome == 'confirmed' and item.meeting_method == Appointment.MeetingMethod.GOOGLE_MEET:
            from .calendar_provider import verified_meet_url
            meet_url = verified_meet_url(provider.get(item.calendar_id, item.event_ids[0]), item)
    except LostClaim:
        return False
    except CalendarUnavailable as exc:
        error = str(exc)[:200]
    except Exception:
        # Neither payloads nor provider messages/tokens go into logs or UI.
        error = "Calendar outcome is unknown; reconcile before releasing this reservation."
    with transaction.atomic():
        lock_bookings()
        current = Appointment.objects.select_for_update().get(pk=item.pk)
        if current.sync_token != item.sync_token:
            # A newer worker owns reconciliation after a stale claim.
            return False
        current.last_sync_at = timezone.now()
        current.last_error = error
        if error:
            if not previously_closed or current.state != original_state:
                current.state = "attention"
        elif (current.cancel_requested or (not was_confirmed and original_deleted(current.previous_id))) and outcome != "canceled":
            current.cancel_requested = True
            # Cancellation arriving during a provider write suppresses confirmation.
            current.state = "pending"
        else:
            current.state = outcome
            if outcome == "confirmed" and current.confirmed_at is None:
                current.confirmed_at = timezone.now()
            if outcome == "confirmed" and item.previous_id and not was_confirmed:
                previous = Appointment.objects.select_for_update().get(pk=item.previous_id)
                previous.state = "canceled"
                previous.save(update_fields=["state"])
                BookingAudit.objects.create(appointment=previous, action="replaced_by_reschedule", details={"replacement": str(item.pk)})
            if outcome == "confirmed":
                current.meet_url = meet_url
                current.buffer_pending_insert = False
                if current.calendar_change_kind:
                    appointment_notices(current, f"{current.calendar_change_kind}-{current.calendar_change_number}")
                    current.calendar_change_kind = ''
                elif not was_confirmed:
                    appointment_notices(current, outcome)
            elif outcome == "canceled" and original_state != "canceled":
                appointment_notices(current, f"canceled-{current.calendar_change_number}" if current.calendar_change_number else outcome)
            if outcome in {"canceled", "completed"}:
                current.calendar_change_kind = ''
                current.buffer_pending_insert = False
            if google_deleted and original_state != "canceled":
                BookingAudit.objects.create(appointment=current, action="google_canceled")
            if outcome != original_state:
                BookingAudit.objects.create(appointment=current, action="calendar_" + outcome)
        current.sync_token = None
        current.sync_started_at = None
        current.save(update_fields=["state", "last_sync_at", "last_error", "confirmed_at", "cancel_requested",
            "buffer_pending_insert", "calendar_change_kind", "sync_token", "sync_started_at", "meet_url"])
    return True
