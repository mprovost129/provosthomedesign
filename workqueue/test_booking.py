import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token, new_intake_token
from .booking import available_times, candidate_times, request_cancel, reserve_appointment, set_booking_access, sync_appointment
from .calendar_provider import EASTERN, CalendarMismatch, CalendarUnavailable, GoogleCalendar, expected_events, moment, validate_event
from .models import Appointment, BookingAccess, BookingAudit, BookingState, EmailAccessToken, NotificationDelivery, WorkItem
from .services import create_work


NOW = datetime(2026, 10, 7, 9, tzinfo=EASTERN)
START = datetime(2026, 10, 8, 10, tzinfo=EASTERN)


class CalendarDouble:
    def __init__(self):
        self.events = {}
        self.external = []
        self.fail_after_save = False
        self.fail_delete = False

    def identity(self, calendar_id):
        return {"id": calendar_id, "timeZone": "America/New_York"}

    def busy(self, start, end, exclude=(), calendar_id=None):
        return self.external + [(moment(event["start"]), moment(event["end"])) for key, event in self.events.items() if key not in exclude]

    def get(self, calendar_id, key):
        return self.events.get(key)

    def ensure(self, appointment, expected):
        if expected["id"] in self.events:
            validate_event(self.events[expected["id"]], expected)
        else:
            self.events[expected["id"]] = {**expected, "etag": "test-only"}
        if self.fail_after_save:
            self.fail_after_save = False
            raise CalendarUnavailable("Calendar request could not be verified.")

    def remove(self, appointment):
        if self.fail_delete:
            raise CalendarUnavailable("Calendar deletion could not be verified.")
        for key in appointment.event_ids:
            self.events.pop(key, None)


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, RECAPTCHA_ENTERPRISE_API_KEY="", RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY="", BOOKING_ENABLED=True, WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True,
    BOOKING_HORIZON_DAYS=60, GOOGLE_CALENDAR_ID="primary", INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com",
    INTAKE_OWNER_EMAIL="owner@example.invalid")
class BookingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.clock = patch("django.utils.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.calendar = CalendarDouble()
        BookingAccess.objects.create(email="alex@example.invalid")
        self.provider = patch("workqueue.booking_views.available_times", side_effect=lambda day, **kw: available_times(day, provider=self.calendar, **kw))
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def reserve(self, email="alex@example.invalid", start=START, **kwargs):
        BookingAccess.objects.get_or_create(email=email)
        return reserve_appointment(email=email, start=start, full_name="Alex Example", phone="508-555-0100",
            purpose="Discuss framing changes", key=kwargs.pop("key", "test:"+uuid.uuid4().hex), provider=self.calendar, **kwargs)[0]

    def signin(self, email="alex@example.invalid"):
        session = self.client.session
        session["queue_verified_email"] = email
        session["queue_email_verified_until"] = (NOW+timedelta(days=7)).timestamp()
        session.save()

    def test_days_times_latest_start_and_notice_are_enforced(self):
        self.assertEqual(candidate_times(date(2026, 10, 12)), [])
        self.assertEqual([time.hour*60+time.minute for time in candidate_times(START.date())], list(range(600, 781, 30)))
        for start in [START.replace(hour=9), START.replace(hour=13, minute=30), START.replace(minute=15), NOW+timedelta(hours=23)]:
            with self.assertRaises(ValidationError):
                self.reserve(start=start)
        self.assertEqual(self.reserve(start=START.replace(hour=13)).reserved_until.hour, 14)

    def test_buffer_blocks_overlap_but_boundary_is_available(self):
        self.reserve()
        with self.assertRaises(ValidationError):
            self.reserve(email="second@example.invalid", start=START+timedelta(minutes=30))
        self.reserve(email="second@example.invalid", start=START+timedelta(hours=1))
        self.assertEqual(Appointment.objects.count(), 2)

    def test_two_per_day_and_one_upcoming_per_client(self):
        self.reserve()
        with self.assertRaises(ValidationError):
            self.reserve(start=START+timedelta(days=1))
        self.reserve(email="second@example.invalid", start=START+timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.reserve(email="third@example.invalid", start=START+timedelta(hours=3))

    def test_calendar_busy_and_all_day_events_block_slots(self):
        self.calendar.external = [(START+timedelta(minutes=45), START+timedelta(hours=1, minutes=15))]
        choices = available_times(START.date(), email="alex@example.invalid", provider=self.calendar)
        self.assertNotIn(START, choices)
        self.assertNotIn(START+timedelta(minutes=30), choices)
        self.calendar.external = [(datetime.combine(START.date(), datetime.min.time(), EASTERN), START+timedelta(days=1))]
        self.assertEqual(available_times(START.date(), email="alex@example.invalid", provider=self.calendar), [])

    def test_daylight_saving_uses_eastern_calendar_date_and_utc_instants(self):
        autumn = datetime(2026, 11, 3, 10, tzinfo=EASTERN)
        self.assertEqual(autumn.utcoffset(), timedelta(hours=-5))
        self.assertEqual(START.utcoffset(), timedelta(hours=-4))
        self.assertEqual(self.reserve(start=autumn).local_date, date(2026, 11, 3))
        spring_clock = datetime(2026, 3, 6, 9, tzinfo=EASTERN)
        spring = candidate_times(date(2026, 3, 10), now=spring_clock)
        self.assertEqual(spring[0].utcoffset(), timedelta(hours=-4))

    def test_retry_returns_same_reservation_without_contacting_calendar(self):
        item = self.reserve(key="same-request")
        with patch.object(self.calendar, "busy", side_effect=CalendarUnavailable):
            same = self.reserve(key="same-request")
        self.assertEqual(item.pk, same.pk)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_new_booking_does_not_add_queue_work_or_change_order(self):
        self.reserve()
        self.assertEqual(WorkItem.objects.count(), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_confirmation_requires_two_verified_calendar_events_then_two_emails(self):
        item = self.reserve()
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "confirmed")
        self.assertEqual(len(self.calendar.events), 2)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        sync_appointment(item.pk, self.calendar)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(len(self.calendar.events), 2)

    def test_partial_calendar_save_recovers_by_stable_id_without_duplicate_or_early_email(self):
        item = self.reserve()
        self.calendar.fail_after_save = True
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "attention")
        self.assertEqual(len(self.calendar.events), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "confirmed")
        self.assertEqual(len(self.calendar.events), 2)

    def test_external_conflict_before_confirmation_holds_capacity_and_sends_no_confirmation(self):
        item = self.reserve()
        self.calendar.external = [(START, START+timedelta(minutes=15))]
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "attention")
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        with self.assertRaises(ValidationError):
            self.reserve(start=START+timedelta(days=1))

    def test_missing_confirmed_event_is_not_silently_recreated(self):
        item = self.reserve()
        sync_appointment(item.pk, self.calendar)
        self.calendar.events.pop(item.event_ids[0])
        sync_appointment(item.pk, self.calendar)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "attention")
        self.assertNotIn(item.event_ids[0], self.calendar.events)

    def test_stale_worker_claim_is_reconciled(self):
        item = self.reserve()
        item.state = "syncing"
        item.sync_started_at = NOW-timedelta(minutes=16)
        item.sync_token = uuid.uuid4()
        item.save()
        self.assertTrue(sync_appointment(item.pk, self.calendar))
        item.refresh_from_db()
        self.assertEqual(item.state, "confirmed")

    def test_old_worker_cannot_overwrite_a_new_reconciliation_claim(self):
        item = self.reserve()
        original = self.calendar.ensure
        def newer_claim(appointment, expected):
            original(appointment, expected)
            Appointment.objects.filter(pk=item.pk).update(sync_token=uuid.uuid4(), state="attention")
        with patch.object(self.calendar, "ensure", side_effect=newer_claim):
            self.assertFalse(sync_appointment(item.pk, self.calendar))
        item.refresh_from_db()
        self.assertEqual(item.state, "attention")
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_primary_calendar_is_resolved_and_saved_as_stable_calendar_identity(self):
        with patch.object(self.calendar, "identity", return_value={"id": "owner@example.invalid", "timeZone": "America/New_York"}):
            item = self.reserve()
        self.assertEqual(item.calendar_id, "owner@example.invalid")

    def test_verified_new_intake_grants_booking_but_does_not_restore_revoked_access(self):
        from .test_client_intake import new_answers
        BookingAccess.objects.filter(email="alex@example.invalid").delete()
        response = self.client.get(reverse("workqueue:submit"))
        token = response.context["form"].initial["intake_token"]
        self.assertEqual(self.client.post(reverse("workqueue:submit"), new_answers(intake_token=token)).status_code, 302)
        self.assertFalse(BookingAccess.objects.filter(email="alex@example.invalid").exists())
        _, raw = mint_email_token("alex@example.invalid")
        self.client.post(reverse("workqueue:confirm_access"), {"token": raw})
        self.assertTrue(BookingAccess.objects.get(email="alex@example.invalid").allowed)
        set_booking_access("alex@example.invalid", False, None)
        _, raw = mint_email_token("alex@example.invalid")
        self.client.post(reverse("workqueue:confirm_access"), {"token": raw})
        self.assertFalse(BookingAccess.objects.get(email="alex@example.invalid").allowed)

    def test_cancel_releases_capacity_only_after_known_calendar_deletion(self):
        item = self.reserve()
        sync_appointment(item.pk, self.calendar)
        request_cancel(item.pk, email=item.email)
        self.calendar.fail_delete = True
        sync_appointment(item.pk, self.calendar)
        with self.assertRaises(ValidationError):
            self.reserve(start=START+timedelta(days=1))
        self.calendar.fail_delete = False
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, "canceled")
        self.assertEqual(len(self.calendar.events), 0)
        self.reserve(start=START+timedelta(days=1))

    def test_reschedule_keeps_original_until_replacement_is_fully_confirmed(self):
        original = self.reserve()
        sync_appointment(original.pk, self.calendar)
        replacement = self.reserve(start=START+timedelta(days=1), previous_id=original.pk)
        self.calendar.fail_after_save = True
        sync_appointment(replacement.pk, self.calendar)
        original.refresh_from_db()
        self.assertEqual(original.state, "confirmed")
        sync_appointment(replacement.pk, self.calendar)
        original.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(original.state, "canceled")
        self.assertEqual(replacement.state, "confirmed")
        self.assertEqual(len(self.calendar.events), 2)

    def test_canceling_pending_reschedule_keeps_original_and_allows_another_attempt(self):
        original = self.reserve()
        sync_appointment(original.pk, self.calendar)
        replacement = self.reserve(start=START+timedelta(days=1), previous_id=original.pk)
        request_cancel(replacement.pk, email=original.email)
        sync_appointment(replacement.pk, self.calendar)
        original.refresh_from_db()
        self.assertEqual(original.state, "confirmed")
        self.reserve(start=START+timedelta(days=1), previous_id=original.pk)

    def test_cancel_during_calendar_update_is_rejected_without_changing_original(self):
        item = self.reserve()
        item.state = "syncing"
        item.save()
        with self.assertRaises(ValidationError):
            request_cancel(item.pk, email=item.email)
        item.refresh_from_db()
        self.assertFalse(item.cancel_requested)

    def test_booking_access_required_and_revocation_does_not_prevent_cancellation(self):
        item = self.reserve()
        set_booking_access(item.email, False, None)
        with self.assertRaises(ValidationError):
            self.reserve(start=START+timedelta(days=1))
        self.assertTrue(request_cancel(item.pk, email=item.email).cancel_requested)

    def test_other_client_cannot_cancel_reschedule_or_attach_foreign_project(self):
        item = self.reserve()
        with self.assertRaises(ValidationError):
            request_cancel(item.pk, email="other@example.invalid")
        with self.assertRaises(ValidationError):
            self.reserve(email="other@example.invalid", start=START+timedelta(days=1), previous_id=item.pk)
        foreign = create_work(data={"contact_full_name": "Other", "company": "Homeowner", "contact_email": "other@example.invalid",
            "contact_phone": "555", "description": "Other work"}, actor=None)[0]
        with self.assertRaises(ValidationError):
            self.reserve(email="other@example.invalid", start=START+timedelta(days=1), project_id=foreign.project_id)

    def test_unverified_client_sees_signin_and_no_appointments_or_calendar_data(self):
        self.reserve()
        response = self.client.get(reverse("workqueue:book"))
        self.assertContains(response, "Email me a sign-in link")
        self.assertNotContains(response, "Discuss framing changes")
        self.assertEqual(self.client.post(reverse("workqueue:cancel_appointment", args=[Appointment.objects.get().pk])).status_code, 404)

    def test_booking_signin_returns_directly_to_booking_without_revealing_eligibility(self):
        for email in ["alex@example.invalid", "unknown@example.invalid"]:
            response = self.client.post(reverse("workqueue:book"), {"email": email})
            self.assertContains(response, "Check your email")
        notice = NotificationDelivery.objects.get(recipient="alex@example.invalid")
        raw = re.search(r"#token=([A-Za-z0-9_-]+)", notice.body)[1]
        self.assertEqual(self.client.post(reverse("workqueue:confirm_access"), {"token": raw}).url, reverse("workqueue:book"))

    @override_settings(BOOKING_CALENDAR_BACKEND="google")
    def test_public_booking_post_is_idempotent_and_waits_for_worker(self):
        self.signin()
        response = self.client.get(reverse("workqueue:book"), {"day": START.date().isoformat()})
        token = response.context["form"].initial["token"]
        data = {"token": token, "day": START.date().isoformat(), "slot": START.isoformat(),
                "full_name": "Alex Example", "phone": "555", "purpose": "Project review", "terms_accepted": "on"}
        with patch("workqueue.booking.calendar_provider", return_value=self.calendar):
            self.assertEqual(self.client.post(reverse("workqueue:book"), data).status_code, 302)
            self.assertEqual(self.client.post(reverse("workqueue:book"), data).status_code, 302)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Appointment.objects.get().state, "pending")
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_forged_or_cross_browser_booking_token_is_rejected(self):
        self.signin()
        response = self.client.get(reverse("workqueue:book"), {"day": START.date().isoformat()})
        other = Client()
        other.get(reverse("workqueue:book"))
        session = other.session
        session["queue_verified_email"] = "alex@example.invalid"
        session["queue_email_verified_until"] = (NOW+timedelta(days=7)).timestamp()
        session.save()
        data = {"token": response.context["form"].initial["token"], "day": START.date().isoformat(),
                "slot": START.isoformat(), "full_name": "Alex", "phone": "555", "purpose": "Review", "terms_accepted": "on"}
        self.assertEqual(other.post(reverse("workqueue:book"), data).status_code, 400)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_expired_verified_session_cannot_book_and_csrf_is_required(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("workqueue:book"), {"email": "alex@example.invalid"}).status_code, 403)
        self.signin()
        session = self.client.session
        session["queue_email_verified_until"] = NOW.timestamp()-1
        session.save()
        self.assertContains(self.client.get(reverse("workqueue:book")), "Verify your email")

    def test_staff_permissions_and_audit_on_same_queue_page(self):
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_workitem", content_type__app_label="workqueue"))
        self.client.force_login(staff)
        data = {"action": "booking_grant", "booking_email": "existing@example.invalid"}
        self.assertEqual(self.client.post(reverse("workqueue:queue"), data).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="change_bookingaccess", content_type__app_label="workqueue"))
        self.assertEqual(self.client.post(reverse("workqueue:queue"), data).status_code, 302)
        self.assertTrue(BookingAccess.objects.get(email="existing@example.invalid").allowed)
        self.assertTrue(BookingAudit.objects.filter(actor=staff).exists())
        self.assertContains(self.client.get(reverse("workqueue:queue")), "existing@example.invalid")

    @override_settings(BOOKING_ENABLED=False)
    def test_disabled_booking_is_hidden_and_routes_are_unavailable(self):
        self.assertEqual(self.client.get(reverse("workqueue:book")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workqueue:submit")), "Book an appointment")


class GoogleAdapterTests(TestCase):
    def test_all_day_busy_uses_the_primary_calendars_timezone(self):
        google = GoogleCalendar()
        payload = {"items": [{"id": "all-day", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-09"}}]}
        with patch.object(google, "identity", return_value={"id": "primary", "timeZone": "America/Los_Angeles"}), patch.object(google, "request", return_value=payload):
            busy = google.busy(START, START+timedelta(hours=1))
        self.assertEqual(busy[0][0].astimezone(EASTERN).hour, 3)

    def test_authorized_api_is_primary_calendar_with_bounded_timeout_and_no_token_logs(self):
        google = GoogleCalendar()
        response = Mock(status_code=200, content=b"{}")
        response.json.return_value = {"kind": "calendar#events"}
        with patch.object(google, "identity", return_value={"id": "primary", "timeZone": "America/New_York"}), patch.object(google, "token", return_value="test-token"), patch("workqueue.calendar_provider.requests.request", return_value=response) as send:
            self.assertEqual(google.busy(START, START+timedelta(hours=1), calendar_id="primary"), [])
        self.assertIn("/calendars/primary/events", send.call_args.args[1])
        self.assertEqual(send.call_args.kwargs["timeout"], 8)

    def test_api_failure_does_not_become_available_time(self):
        google = GoogleCalendar()
        with patch.object(google, "request", side_effect=CalendarUnavailable), self.assertRaises(CalendarUnavailable):
            google.busy(START, START+timedelta(hours=1))

    def test_all_day_and_recurring_expanded_events_are_busy_and_transparent_events_are_ignored(self):
        google = GoogleCalendar()
        payload = {"items": [{"id": "all-day", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-09"}},
            {"id": "recurring-instance", "start": {"dateTime": START.isoformat()}, "end": {"dateTime": (START+timedelta(minutes=30)).isoformat()}},
            {"id": "free", "transparency": "transparent"}]}
        with patch.object(google, "identity", return_value={"id": "primary", "timeZone": "America/New_York"}), patch.object(google, "request", return_value=payload) as send:
            self.assertEqual(len(google.busy(START, START+timedelta(hours=1))), 2)
        self.assertEqual(send.call_args.kwargs["params"]["singleEvents"], "true")

    def test_event_collision_and_external_edit_are_rejected(self):
        item = Appointment(id=uuid.uuid4(), full_name="Alex", phone="555", email="alex@example.invalid", purpose="Review",
            starts_at=START, ends_at=START+timedelta(minutes=30), reserved_until=START+timedelta(hours=1))
        event = expected_events(item)[0]
        with self.assertRaises(CalendarMismatch):
            validate_event({**event, "extendedProperties": {"private": {"phdAppointment": "foreign"}}}, event)
        with self.assertRaises(CalendarMismatch):
            validate_event({**event, "start": {"dateTime": (START+timedelta(minutes=30)).isoformat()}}, event)

    def test_delete_requires_owned_event_and_checks_etag(self):
        item = Appointment(id=uuid.uuid4(), calendar_id="primary", full_name="Alex", phone="555", email="alex@example.invalid", purpose="Review",
            starts_at=START, ends_at=START+timedelta(minutes=30), reserved_until=START+timedelta(hours=1))
        google = GoogleCalendar()
        event = {**expected_events(item)[0], "etag": '"version-a"'}
        with patch.object(google, "get", side_effect=[event, None, None, None]), patch.object(google, "request") as call:
            google.remove(item)
        self.assertEqual(call.call_args.kwargs["headers"]["If-Match"], '"version-a"')


@skipUnless(connection.vendor == "postgresql", "Booking concurrency requires a dedicated local PostgreSQL database")
class BookingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        BookingState.objects.get_or_create(pk=1)

    def reserve_one(self, email, start):
        close_old_connections()
        try:
            reserve_appointment(email=email, start=start, full_name="Example", phone="555", purpose="Review",
                key=uuid.uuid4().hex, provider=CalendarDouble())
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    def test_parallel_clients_cannot_reserve_same_slot(self):
        for email in ["one@example.invalid", "two@example.invalid"]:
            BookingAccess.objects.create(email=email)
        with patch("django.utils.timezone.now", return_value=NOW), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda email: self.reserve_one(email, START), ["one@example.invalid", "two@example.invalid"]))
        self.assertEqual(sum(results), 1)

    def test_parallel_clients_cannot_exceed_daily_limit(self):
        for email in ["one@example.invalid", "two@example.invalid", "three@example.invalid"]:
            BookingAccess.objects.create(email=email)
        with patch("django.utils.timezone.now", return_value=NOW), ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda pair: self.reserve_one(*pair), [("one@example.invalid", START),
                ("two@example.invalid", START+timedelta(hours=1)), ("three@example.invalid", START+timedelta(hours=2))]))
        self.assertEqual(sum(results), 2)
