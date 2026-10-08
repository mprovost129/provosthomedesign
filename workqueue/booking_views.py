import uuid
from datetime import datetime, timedelta
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import authorized_projects, authorized_work, new_intake_token, read_intake_token, session_digest, verified_email
from .booking import (HOLD_STATES, available_times, eligible, lock_bookings, request_cancel,
                      reserve_appointment, set_booking_access, sync_appointment)
from .calendar_provider import EASTERN, CalendarUnavailable
from .client_views import limited
from .client_security import verify_form
from .models import Appointment, BookingAudit, NotificationDelivery
from .notifications import queue_signin


def booking_enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "BOOKING_ENABLED", False) or not getattr(settings, "WORK_INTAKE_ENABLED", False):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


class AppointmentForm(forms.Form):
    terms_accepted = forms.BooleanField(label="Terms accepted", required=True,
        error_messages={"required": "Please agree to the Terms & Conditions before requesting an appointment."})
    token = forms.CharField(widget=forms.HiddenInput)
    day = forms.DateField(label="Appointment date", widget=forms.DateInput(attrs={"type": "date"}))
    slot = forms.ChoiceField(label="Available start time (Eastern)")
    full_name = forms.CharField(label="Full name", max_length=200)
    phone = forms.CharField(label="Phone number", max_length=50)
    purpose = forms.CharField(label="What would you like to discuss?", max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))
    project = forms.ModelChoiceField(queryset=None, label="Related project, if applicable", required=False)

    def __init__(self, *args, slots=(), email="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slot"].choices = [("", "Choose a time"), *[(start.isoformat(), start.strftime("%I:%M %p").lstrip("0")) for start in slots]]
        self.fields["project"].queryset = authorized_projects(email)


@booking_enabled
@never_cache
@require_http_methods(["GET", "POST"])
def book(request):
    email = verified_email(request)
    access_form = forms.Form()
    access_form.fields["email"] = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    sent = False
    if not email and request.method == "POST":
        access_form = forms.Form(request.POST)
        access_form.fields["email"] = forms.EmailField(label="Email address")
        if verify_form(request, access_form, "work_booking_signin"):
            target = access_form.cleaned_data["email"].strip().lower()
            if limited(request, "signin-ip", limit=20, window=900) and limited(request, "signin-email", target, limit=5, window=900):
                queue_signin(target, destination="booking")
            sent = True
    permitted = eligible(email)
    previous = None
    previous_id = request.GET.get("reschedule", "")
    if previous_id and email:
        try:
            previous_id = uuid.UUID(previous_id)
        except ValueError:
            raise Http404
        previous = get_object_or_404(Appointment, pk=previous_id, email=email)
        if request.method == "GET" and (previous.state != "confirmed" or previous.cancel_requested or previous.starts_at <= timezone.now()):
            messages.error(request, "Review your upcoming appointment before rescheduling.")
            return redirect("workqueue:book")
    if request.method == "POST" and email and permitted:
        try:
            nonce = read_intake_token(request, request.POST.get("token", ""))
        except ValidationError:
            pass
        else:
            key = f"booking:{session_digest(request)}:{nonce}"
            if Appointment.objects.filter(email=email, idempotency_key=key).exists():
                return redirect("workqueue:book")
    raw_day = request.POST.get("day") if request.method == "POST" else request.GET.get("day")
    day_error = ""
    try:
        day = forms.DateField().clean(raw_day) if raw_day else timezone.now().astimezone(EASTERN).date()+timedelta(days=1)
    except ValidationError:
        day = timezone.now().astimezone(EASTERN).date()+timedelta(days=1)
        day_error = "Choose a valid date."
    initial = {"token": new_intake_token(request), "day": day}
    recent = authorized_work(email).filter(submissions__owner_email=email).order_by("-received_at").first() if email else None
    if recent:
        initial.update(full_name=recent.contact_full_name, phone=recent.contact_phone)
    if previous:
        initial.update(full_name=previous.full_name, phone=previous.phone, purpose=previous.purpose, project=previous.project_id)
    slots, unavailable = [], ""
    has_upcoming = bool(email and Appointment.objects.filter(email=email, state__in=HOLD_STATES, reserved_until__gt=timezone.now()).exists())
    if permitted and not day_error and (previous or not has_upcoming):
        try:
            slots = available_times(day, email=email, previous=previous)
        except CalendarUnavailable:
            unavailable = "We cannot check calendar availability right now. Please try again later."
    form = AppointmentForm(request.POST or None, initial=initial, slots=slots, email=email)
    status = 200
    if request.method == "POST":
        if not email:
            return render(request, "workqueue/booking.html", {"access_form": access_form, "sent": sent,
                "preview": settings.BOOKING_CALENDAR_BACKEND == "preview"}, status=200 if sent else 400)
        if not permitted:
            raise PermissionDenied
        if day_error or unavailable:
            form.add_error(None, day_error or unavailable)
        elif verify_form(request, form, "work_booking"):
            try:
                nonce = read_intake_token(request, form.cleaned_data["token"])
                if not limited(request, "booking", email, limit=20):
                    raise ValidationError("Too many booking attempts. Please try again later.")
                item, created = reserve_appointment(email=email, start=datetime.fromisoformat(form.cleaned_data["slot"]),
                    full_name=form.cleaned_data["full_name"], phone=form.cleaned_data["phone"], purpose=form.cleaned_data["purpose"],
                    project_id=form.cleaned_data["project"].pk if form.cleaned_data["project"] else None,
                    previous_id=previous.pk if previous else None, key=f"booking:{session_digest(request)}:{nonce}",
                    terms_url=reverse("pages:terms"))
            except (ValidationError, CalendarUnavailable) as exc:
                form.add_error(None, " ".join(exc.messages) if isinstance(exc, ValidationError) else "Calendar unavailable. Please try again later.")
            else:
                # Fictitious local preview is immediate. Production is a durable worker job.
                if settings.BOOKING_CALENDAR_BACKEND == "preview" and settings.INTAKE_LOCAL_DEVELOPMENT:
                    sync_appointment(item.pk)
                messages.success(request, "Your appointment request was saved. Review its confirmation below.")
                return redirect("workqueue:book")
        status = 400
    appointments = Appointment.objects.filter(email=email).select_related("project").prefetch_related("replacements").annotate(
        display_order=Case(When(state__in=HOLD_STATES, then=Value(0)), default=Value(1), output_field=IntegerField())).order_by("display_order", "-starts_at")[:20] if email else []
    records = list(appointments)
    return render(request, "workqueue/booking.html", {"client_email": email, "permitted": permitted, "form": form,
        "day": day, "day_error": day_error, "unavailable": unavailable, "slots": slots, "previous": previous,
        "access_form": access_form, "sent": sent, "has_upcoming": has_upcoming,
        "appointments": [item for item in records if item.state in HOLD_STATES],
        "closed_appointments": [item for item in records if item.state not in HOLD_STATES],
        "preview": settings.BOOKING_CALENDAR_BACKEND == "preview"}, status=status)


@booking_enabled
@never_cache
@require_http_methods(["POST"])
def cancel(request, appointment_id):
    email = verified_email(request)
    item = get_object_or_404(Appointment, pk=appointment_id, email=email) if email else None
    if not item:
        raise Http404
    try:
        request_cancel(item.pk, email=email)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        if settings.BOOKING_CALENDAR_BACKEND == "preview" and settings.INTAKE_LOCAL_DEVELOPMENT:
            sync_appointment(item.pk)
        messages.success(request, "Cancellation requested. Capacity is released once the calendar change is confirmed.")
    return redirect("workqueue:book")


def staff_action(request):
    """Called by the existing protected queue view; all writes stay on that page."""
    action = request.POST.get("action")
    permission = "workqueue.change_bookingaccess" if action in ("booking_grant", "booking_revoke") else "workqueue.change_appointment"
    if not request.user.has_perm(permission):
        raise PermissionDenied
    try:
        if action in ("booking_grant", "booking_revoke"):
            set_booking_access(request.POST.get("booking_email", ""), action == "booking_grant", request.user)
        else:
            try:
                appointment_id = uuid.UUID(request.POST.get("appointment_id", ""))
            except ValueError:
                raise Http404
            item = get_object_or_404(Appointment, pk=appointment_id)
            if action == "booking_cancel":
                request_cancel(item.pk, actor=request.user)
            elif action == "booking_retry":
                with transaction.atomic():
                    lock_bookings()
                    item = Appointment.objects.select_for_update().get(pk=item.pk)
                    if item.state != "attention":
                        raise ValidationError("Only appointments needing a calendar check can be retried.")
                    item.last_sync_at = None
                    item.save(update_fields=["last_sync_at"])
                    BookingAudit.objects.create(appointment=item, actor=request.user, action="calendar_retry_requested")
            elif action == "booking_retry_email":
                with transaction.atomic():
                    notice = get_object_or_404(NotificationDelivery.objects.select_for_update(), appointment=item,
                                              pk=request.POST.get("notice_id"))
                    if notice.state not in ("failed", "unknown"):
                        raise ValidationError("This email cannot be retried in its current state.")
                    if notice.state == "unknown" and request.POST.get("not_sent_confirmed") != "yes":
                        raise ValidationError("Check provider logs and confirm this email was not delivered before resending.")
                    notice.state = "pending"
                    notice.last_error = ""
                    notice.save(update_fields=["state", "last_error"])
                    BookingAudit.objects.create(appointment=item, actor=request.user, action="email_retry_requested", details={"delivery_id": notice.pk})
            else:
                raise Http404
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Appointment settings saved.")
    return redirect(reverse("workqueue:queue") + "#appointments")
