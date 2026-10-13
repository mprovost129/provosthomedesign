import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from . import test_booking as fixture
from .booking import available_times, appointment_notices, request_cancel, sync_appointment
from .calendar_provider import CalendarUnavailable, CalendarMismatch, GoogleCalendar, expected_events, moment
from .calendar_reconciliation import cancel_obsolete_appointment_notice
from .models import Appointment, BookingAudit, NotificationDelivery


@override_settings(BOOKING_ENABLED=True, WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True,
    BOOKING_HORIZON_DAYS=60, GOOGLE_CALENDAR_ID='primary', INTAKE_OWNER_EMAIL='owner@example.invalid',
    INTAKE_PUBLIC_BASE_URL='https://testserver', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CalendarReconciliationTests(TestCase):
    setUp = fixture.BookingTests.setUp
    reserve = fixture.BookingTests.reserve
    signin = fixture.BookingTests.signin

    def confirmed(self):
        item = self.reserve()
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        return item

    def move(self, item, *, start=None, duration=30):
        start = start or fixture.START+timedelta(days=1)
        event = copy.deepcopy(self.calendar.events[item.event_ids[0]])
        event['start'] = {'dateTime':start.isoformat()}
        event['end'] = {'dateTime':(start+timedelta(minutes=duration)).isoformat()}
        event['etag'] = uuid.uuid4().hex
        self.calendar.events[item.event_ids[0]] = event
        return event

    def test_google_deletion_cancels_booking_clears_buffer_and_releases_capacity(self):
        item = self.confirmed()
        self.calendar.events.pop(item.event_ids[0])
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertEqual(self.calendar.events, {})
        self.assertIn(fixture.START, available_times(fixture.START.date(), email=item.email, provider=self.calendar))
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled').count(), 2)
        sync_appointment(item.pk, self.calendar)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled').count(), 2)
        self.assertEqual(BookingAudit.objects.filter(action='google_canceled').count(), 1)

    def test_google_cancelled_tombstone_without_details_is_a_deletion(self):
        item = self.confirmed()
        self.calendar.events[item.event_ids[0]] = {'id':item.event_ids[0], 'status':'cancelled'}
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertNotIn(item.event_ids[1], self.calendar.events)

    def test_google_move_updates_app_date_buffer_and_one_notice_pair(self):
        item = self.confirmed()
        moved = self.move(item)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(item.starts_at, moment(moved['start']))
        self.assertEqual(item.local_date, fixture.START.date()+timedelta(days=1))
        self.assertEqual(moment(self.calendar.events[item.event_ids[1]]['start']), item.ends_at)
        self.assertEqual(moment(self.calendar.events[item.event_ids[1]]['end']), item.reserved_until)
        self.assertEqual(item.calendar_change_number, 1)
        self.assertEqual(item.calendar_change_kind, '')
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 2)
        sync_appointment(item.pk, self.calendar)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 2)
        self.assertIn(fixture.START, available_times(fixture.START.date(), email='other@example.invalid', provider=self.calendar))

    def test_google_length_change_supports_hour_meeting_and_full_buffer(self):
        item = self.confirmed()
        self.move(item, start=fixture.START, duration=60)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.duration_minutes, 60)
        self.assertEqual(item.reserved_until, fixture.START+timedelta(minutes=90))
        notice = NotificationDelivery.objects.filter(appointment_event='changed-1', recipient_kind='client').get()
        self.assertIn('Meeting length: 60 minutes', notice.body)
        self.assertNotIn(fixture.START+timedelta(hours=1), available_times(fixture.START.date(), email='other@example.invalid', provider=self.calendar))

    def test_manual_google_move_outside_public_hours_is_preserved(self):
        item = self.confirmed()
        start = fixture.START.replace(hour=16)
        self.move(item, start=start)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.starts_at, start)
        self.assertEqual(item.state, 'confirmed')

    def test_missing_buffer_is_repaired_with_persisted_retry_safe_id(self):
        item = self.confirmed(); old_key = item.event_ids[1]
        self.calendar.events.pop(old_key)
        with patch.object(self.calendar, 'reconcile_buffer', side_effect=CalendarUnavailable('Unknown buffer outcome')):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); new_key = item.event_ids[1]
        self.assertNotEqual(new_key, old_key)
        self.assertTrue(item.buffer_pending_insert)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.event_ids[1], new_key)
        self.assertFalse(item.buffer_pending_insert)
        self.assertEqual(len(self.calendar.events), 2)

    def test_buffer_timeout_after_save_recovers_without_extra_buffer_or_notices(self):
        item = self.confirmed(); self.move(item)
        original = self.calendar.reconcile_buffer
        def save_then_timeout(item, expected):
            original(item, expected)
            raise CalendarUnavailable('Unknown buffer outcome')
        with patch.object(self.calendar, 'reconcile_buffer', side_effect=save_then_timeout):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertEqual(item.calendar_change_kind, 'changed')
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 0)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(item.calendar_change_number, 1)
        self.assertEqual(len(self.calendar.events), 2)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 2)

    def test_connection_failure_does_not_cancel_or_release_existing_booking(self):
        item = self.confirmed()
        with patch.object(self.calendar, 'get', side_effect=CalendarUnavailable('Connection unavailable')):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertEqual(item.starts_at, fixture.START)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled').count(), 0)

    def test_calendar_not_found_is_not_treated_as_deleted_meeting(self):
        item = self.confirmed(); self.calendar.events.pop(item.event_ids[0])
        with patch.object(self.calendar, 'identity', side_effect=CalendarUnavailable('Calendar cannot be verified')):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertIn(item.event_ids[1], self.calendar.events)

    def test_orphan_cleanup_failure_holds_reservation_until_cleanup_verified(self):
        item = self.confirmed(); self.calendar.events.pop(item.event_ids[0])
        self.calendar.fail_delete = True
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); self.assertEqual(item.state, 'attention')
        self.calendar.fail_delete = False
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); self.assertEqual(item.state, 'canceled')

    def test_foreign_identity_or_invalid_duration_never_overwrites_app(self):
        item = self.confirmed()
        original = copy.deepcopy(self.calendar.events[item.event_ids[0]])
        for change in [dict(extendedProperties={'private':{'phdAppointment':str(uuid.uuid4())}}),
                dict(recurrence=['RRULE:FREQ=DAILY']), dict(start={'date':'2026-10-08'}),
                dict(end={'dateTime':(fixture.START+timedelta(days=2)).isoformat()}), dict(etag='')]:
            self.calendar.events[item.event_ids[0]] = {**original, **change}
            sync_appointment(item.pk, self.calendar)
            item.refresh_from_db()
            self.assertEqual(item.state, 'attention')
            self.assertEqual(item.starts_at, fixture.START)
        self.assertEqual(item.calendar_change_number, 0)

    def test_busy_conflict_keeps_observed_google_time_reserved_for_review(self):
        item = self.confirmed(); moved = self.move(item)
        start = moment(moved['start'])
        self.calendar.external = [(start, start+timedelta(minutes=10))]
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertEqual(item.starts_at, start)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 0)

    def test_main_restored_during_cleanup_is_not_deleted_or_marked_canceled(self):
        item = self.confirmed()
        original_main = copy.deepcopy(self.calendar.events.pop(item.event_ids[0]))
        remove = self.calendar.remove_buffer
        def restore_during_cleanup(item):
            remove(item)
            self.calendar.events[item.event_ids[0]] = original_main
        with patch.object(self.calendar, 'remove_buffer', side_effect=restore_during_cleanup):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertIn(item.event_ids[0], self.calendar.events)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled').count(), 0)
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); self.assertEqual(item.state, 'confirmed')

    def test_main_changed_again_during_buffer_update_recovers_latest_time(self):
        item = self.confirmed(); self.move(item)
        original = self.calendar.reconcile_buffer
        def move_again(item, expected):
            original(item, expected)
            self.move(item, start=fixture.START+timedelta(days=1, hours=1))
        with patch.object(self.calendar, 'reconcile_buffer', side_effect=move_again):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); self.assertEqual(item.state, 'attention')
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.starts_at, fixture.START+timedelta(days=1,hours=1))
        self.assertEqual(item.calendar_change_number, 2)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-2').count(), 2)
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='changed-1').count(), 0)

    def test_calendar_restore_and_second_deletion_each_get_one_notice_pair(self):
        item = self.confirmed()
        main = copy.deepcopy(self.calendar.events.pop(item.event_ids[0]))
        sync_appointment(item.pk, self.calendar)
        self.calendar.events[item.event_ids[0]] = main
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='restored-1').count(), 2)
        self.calendar.events.pop(item.event_ids[0])
        sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertEqual(NotificationDelivery.objects.filter(appointment_event='canceled-1').count(), 2)

    def test_app_cancel_and_client_reschedule_still_write_google(self):
        original = self.confirmed()
        replacement = self.reserve(start=fixture.START+timedelta(days=1), previous_id=original.pk)
        sync_appointment(replacement.pk, self.calendar)
        original.refresh_from_db(); replacement.refresh_from_db()
        self.assertEqual(original.state, 'canceled')
        self.assertIn(replacement.event_ids[0], self.calendar.events)
        request_cancel(replacement.pk, email=replacement.email)
        sync_appointment(replacement.pk, self.calendar)
        replacement.refresh_from_db()
        self.assertEqual(replacement.state, 'canceled')
        self.assertEqual(self.calendar.events, {})

    def test_google_deleting_original_stops_pending_reschedule_from_recreating_meeting(self):
        original = self.confirmed()
        replacement = self.reserve(start=fixture.START+timedelta(days=1), previous_id=original.pk)
        self.calendar.events.pop(original.event_ids[0])
        sync_appointment(original.pk, self.calendar)
        sync_appointment(replacement.pk, self.calendar)
        replacement.refresh_from_db()
        self.assertEqual(replacement.state, 'canceled')
        self.assertEqual(self.calendar.events, {})

    def test_restore_of_old_rescheduled_meeting_is_not_overwritten_by_replacement_poll(self):
        original = self.confirmed()
        original_main = copy.deepcopy(self.calendar.events[original.event_ids[0]])
        replacement = self.reserve(start=fixture.START+timedelta(days=1), previous_id=original.pk)
        sync_appointment(replacement.pk, self.calendar)
        self.calendar.events[original.event_ids[0]] = original_main
        sync_appointment(original.pk, self.calendar)
        sync_appointment(replacement.pk, self.calendar)
        original.refresh_from_db(); replacement.refresh_from_db()
        self.assertEqual(original.state, 'confirmed')
        self.assertEqual(replacement.state, 'confirmed')

    def test_old_worker_claim_cannot_adopt_new_time(self):
        item = self.confirmed(); self.move(item)
        get = self.calendar.get
        def lost_claim(calendar, key):
            event = get(calendar, key)
            if key == item.event_ids[0]:
                Appointment.objects.filter(pk=item.pk).update(sync_token=uuid.uuid4(), state='attention')
            return event
        with patch.object(self.calendar, 'get', side_effect=lost_claim):
            self.assertFalse(sync_appointment(item.pk, self.calendar))
        item.refresh_from_db()
        self.assertEqual(item.starts_at, fixture.START)

    def test_superseded_confirmations_and_changes_are_not_sent(self):
        item = self.confirmed(); self.move(item)
        first_notice = NotificationDelivery.objects.filter(appointment_event='confirmed', recipient_kind='client').get()
        sync_appointment(item.pk, self.calendar)
        self.assertTrue(cancel_obsolete_appointment_notice(first_notice))
        changed = NotificationDelivery.objects.filter(appointment_event='changed-1', recipient_kind='client').get()
        self.assertFalse(cancel_obsolete_appointment_notice(changed))
        request_cancel(item.pk, email=item.email); sync_appointment(item.pk, self.calendar)
        self.assertTrue(cancel_obsolete_appointment_notice(changed))

    def test_new_times_and_duration_show_in_client_booking_page(self):
        item = self.confirmed(); self.move(item, duration=60)
        sync_appointment(item.pk, self.calendar); self.signin()
        from django.urls import reverse
        response = self.client.get(reverse('workqueue:book'))
        self.assertContains(response, '60-minute meeting')
        self.assertContains(response, 'Friday, October 9, 2026')

    def test_worker_polls_after_45_seconds_instead_of_five_minutes(self):
        item = self.confirmed()
        Appointment.objects.filter(pk=item.pk).update(last_sync_at=fixture.NOW-timedelta(seconds=50))
        with patch('workqueue.management.commands.sync_queue_appointments.calendar_provider', return_value=self.calendar):
            call_command('sync_queue_appointments', verbosity=0)
        item.refresh_from_db()
        self.assertEqual(item.last_sync_at, fixture.NOW)

    def test_google_buffer_patch_is_conditional_and_leaves_other_fields_untouched(self):
        item = self.confirmed()
        actual = copy.deepcopy(self.calendar.events[item.event_ids[1]])
        actual['etag'] = 'buffer-v1'
        item.starts_at += timedelta(hours=1); item.ends_at += timedelta(hours=1); item.reserved_until += timedelta(hours=1)
        expected = expected_events(item)[1]
        provider = GoogleCalendar()
        provider.get = Mock(side_effect=[actual, {**expected, 'etag':'buffer-v2'}])
        provider.request = Mock()
        provider.reconcile_buffer(item, expected)
        call = provider.request.call_args
        self.assertEqual(call.args[0], 'PATCH')
        self.assertEqual(call.kwargs['headers'], {'If-Match':'buffer-v1'})
        self.assertEqual(set(call.kwargs['json']), {'start','end','transparency','visibility'})

    def test_google_buffer_patch_never_overwrites_etag_conflict(self):
        item = self.confirmed()
        actual = copy.deepcopy(self.calendar.events[item.event_ids[1]])
        item.ends_at += timedelta(minutes=30); item.reserved_until += timedelta(minutes=30)
        provider = GoogleCalendar(); provider.get = Mock(return_value=actual)
        provider.request = Mock(side_effect=CalendarUnavailable('Conditional update failed'))
        with self.assertRaises(CalendarUnavailable): provider.reconcile_buffer(item, expected_events(item)[1])
        self.assertEqual(provider.request.call_count, 1)

    def test_google_buffer_cleanup_never_deletes_primary_event(self):
        item = self.confirmed()
        expected = expected_events(item)[1]
        provider = GoogleCalendar(); provider.get = Mock(side_effect=[{**expected,'etag':'v1'}, None]); provider.request = Mock()
        provider.remove_buffer(item)
        self.assertEqual(provider.request.call_args.args[0], 'DELETE')
        self.assertTrue(provider.request.call_args.args[1].endswith(item.event_ids[1]))
        self.assertEqual(provider.request.call_args.kwargs['headers'], {'If-Match':'v1'})

    def test_watching_a_canceled_meeting_never_holds_its_old_capacity(self):
        item = self.confirmed(); self.calendar.events.pop(item.event_ids[0])
        sync_appointment(item.pk, self.calendar)
        original_get = self.calendar.get
        def inspect(calendar, key):
            current = Appointment.objects.get(pk=item.pk)
            self.assertEqual(current.state, 'canceled')
            self.assertFalse(Appointment.objects.filter(pk=item.pk, state__in=['pending','syncing','confirmed','attention']).exists())
            return original_get(calendar, key)
        with patch.object(self.calendar, 'get', side_effect=inspect): sync_appointment(item.pk, self.calendar)
        item.refresh_from_db(); self.assertIsNone(item.sync_token)

    def test_connection_failure_when_watching_canceled_record_does_not_reactivate_it(self):
        item = self.confirmed(); self.calendar.events.pop(item.event_ids[0])
        sync_appointment(item.pk, self.calendar)
        with patch.object(self.calendar, 'get', side_effect=CalendarUnavailable('Connection unavailable')):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertIsNone(item.sync_token)

    def test_restore_verified_but_buffer_write_unknown_holds_restored_time(self):
        item = self.confirmed(); main = copy.deepcopy(self.calendar.events.pop(item.event_ids[0]))
        sync_appointment(item.pk, self.calendar)
        self.calendar.events[item.event_ids[0]] = main
        with patch.object(self.calendar, 'reconcile_buffer', side_effect=CalendarUnavailable('Unknown buffer write')):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertEqual(item.calendar_change_kind, 'restored')

    def test_client_reschedule_preserves_google_extended_meeting_length(self):
        item = self.confirmed(); self.move(item, start=fixture.START, duration=60)
        sync_appointment(item.pk, self.calendar); item.refresh_from_db()
        start = fixture.START+timedelta(days=1)
        replacement = self.reserve(start=start, previous_id=item.pk)
        self.assertEqual(replacement.duration_minutes, 60)
        self.assertEqual(replacement.reserved_until, start+timedelta(minutes=90))
        sync_appointment(replacement.pk, self.calendar)
        self.assertEqual(moment(self.calendar.events[replacement.event_ids[0]]['end']), replacement.ends_at)

    def test_rescheduling_longer_meeting_excludes_late_slots_and_rechecks_full_hold(self):
        item = self.confirmed(); self.move(item, start=fixture.START, duration=60)
        sync_appointment(item.pk, self.calendar); item.refresh_from_db()
        day = fixture.START.date()+timedelta(days=1)
        slots = available_times(day, email=item.email, previous=item, provider=self.calendar)
        late = (fixture.START+timedelta(days=1)).replace(hour=13)
        self.assertNotIn(late, slots)
        self.assertIn(late-timedelta(minutes=30), slots)
        self.calendar.external = [(late-timedelta(minutes=10),late)]
        self.assertNotIn(late-timedelta(minutes=30), available_times(day, email=item.email, previous=item, provider=self.calendar))
