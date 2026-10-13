from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from . import test_booking as fixture
from .booking import sync_appointment
from .models import Appointment, BookingAudit, BookingState, NotificationDelivery


@override_settings(BOOKING_ENABLED=True, BOOKING_HORIZON_DAYS=60, GOOGLE_CALENDAR_ID='primary',
    INTAKE_OWNER_EMAIL='owner@example.invalid', INTAKE_PUBLIC_BASE_URL='https://testserver')
class CalendarSyncConcurrencyTests(TransactionTestCase):
    def setUp(self):
        BookingState.objects.get_or_create(pk=1)
        fixture.BookingTests.setUp(self)
    reserve = fixture.BookingTests.reserve

    def race(self, item):
        if connection.vendor != 'postgresql':
            self.skipTest('Production locking requires PostgreSQL.')
        barrier = Barrier(2)
        def run(_):
            try:
                barrier.wait(timeout=10)
                return sync_appointment(item.pk, self.calendar)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def confirmed(self):
        item = self.reserve(); sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); return item

    def test_two_workers_observing_one_move_create_one_notice_pair(self):
        import copy
        from datetime import timedelta
        item = self.confirmed()
        event = copy.deepcopy(self.calendar.events[item.event_ids[0]])
        start = fixture.START+timedelta(days=1)
        event.update(start={'dateTime':start.isoformat()}, end={'dateTime':(start+timedelta(minutes=30)).isoformat()}, etag='moved')
        self.calendar.events[item.event_ids[0]] = event
        self.race(item); item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(item.starts_at, start)
        self.assertEqual(item.calendar_change_number, 1)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 2)
        self.assertEqual(BookingAudit.objects.filter(action='google_times_observed').count(), 1)

    def test_two_workers_observing_one_delete_create_one_cancellation_pair(self):
        item = self.confirmed()
        self.calendar.events.pop(item.event_ids[0])
        self.race(item); item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertEqual(self.calendar.events, {})
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled').count(), 2)
        self.assertEqual(BookingAudit.objects.filter(action='google_canceled').count(), 1)

    def test_two_workers_repair_missing_buffer_without_duplicate_ids(self):
        item = self.confirmed()
        self.calendar.events.pop(item.event_ids[1])
        self.race(item); item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(len(self.calendar.events), 2)
        self.assertIn(item.event_ids[1], self.calendar.events)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
