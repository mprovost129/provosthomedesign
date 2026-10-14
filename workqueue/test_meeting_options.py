import copy
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .booking import sync_appointment, request_cancel
from .booking_views import AppointmentForm
from .calendar_provider import CalendarUnavailable, GoogleCalendar, expected_events, verified_meet_url
from .models import Appointment, NotificationDelivery
from . import test_booking as baseline
from .test_booking import CalendarDouble, NOW, START

ADDRESS = '7 Park St. Unit 1, Rehoboth, MA 02769'
URL = 'https://meet.google.com/abc-defg-hij'


def conference(status='success', uri=URL):
    return {'createRequest': {'status': {'statusCode': status}},
        'conferenceSolution': {'key': {'type': 'hangoutsMeet'}},
        'entryPoints': [{'entryPointType': 'video', 'uri': uri}]}


class MeetCalendarDouble(CalendarDouble):
    status = 'success'

    def ensure(self, appointment, expected):
        super().ensure(appointment, expected)
        if 'conferenceData' in expected:
            self.events[expected['id']]['conferenceData'] = conference(self.status)


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, RECAPTCHA_ENTERPRISE_API_KEY='',
    RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='', BOOKING_ENABLED=True, WORK_INTAKE_ENABLED=True,
    WORK_QUEUE_ENABLED=True, BOOKING_HORIZON_DAYS=60, GOOGLE_CALENDAR_ID='primary',
    INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com', INTAKE_OWNER_EMAIL='owner@example.invalid',
    CONTACT_ADDRESS=ADDRESS)
class MeetingOptionsTests(TestCase):
    setUp = baseline.BookingTests.setUp
    reserve = baseline.BookingTests.reserve
    signin = baseline.BookingTests.signin

    def test_method_is_required_and_rejects_forged_choice(self):
        for choice in ['', 'zoom']:
            form = AppointmentForm({'meeting_method': choice}, email='alex@example.invalid')
            self.assertFalse(form.is_valid())
            self.assertIn('meeting_method', form.errors)
        with self.assertRaises(ValidationError):
            self.reserve(meeting_method='zoom')

    def test_in_person_address_is_snapshotted_in_calendar_and_both_emails(self):
        item = self.reserve(meeting_method='in_person')
        with override_settings(CONTACT_ADDRESS='Another address'):
            sync_appointment(item.pk, self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.meeting_address, ADDRESS)
        events = expected_events(item)
        self.assertEqual(events[0]['location'], ADDRESS)
        self.assertNotIn('location', events[1])
        self.assertNotIn('conferenceData', events[0])
        for notice in item.notifications.all():
            self.assertIn(ADDRESS, notice.body)
            self.assertIn('Meeting type: In person', notice.body)
        self.assertEqual(item.notifications.count(), 2)

    def test_phone_instruction_uses_client_number_without_meet(self):
        item = self.reserve(meeting_method='phone')
        sync_appointment(item.pk, self.calendar)
        self.assertIn(item.phone, expected_events(item)[0]['location'])
        self.assertNotIn('conferenceData', expected_events(item)[0])
        for notice in item.notifications.all():
            self.assertIn('Mike will call you at ' + item.phone, notice.body)
            self.assertNotIn('Office address:', notice.body)

    def test_meet_requests_unique_primary_conference_never_buffer_conference(self):
        first = self.reserve(meeting_method='google_meet')
        second = self.reserve(email='second@example.invalid', start=START+timedelta(hours=1), meeting_method='google_meet')
        request = expected_events(first)[0]['conferenceData']['createRequest']
        self.assertEqual(request['conferenceSolutionKey']['type'], 'hangoutsMeet')
        self.assertEqual(request, expected_events(first)[0]['conferenceData']['createRequest'])
        self.assertNotEqual(request['requestId'], expected_events(second)[0]['conferenceData']['createRequest']['requestId'])
        self.assertNotIn('conferenceData', expected_events(first)[1])

    def test_meet_pending_holds_slot_and_sends_no_confirmation_then_retry_succeeds(self):
        calendar = MeetCalendarDouble()
        calendar.status = 'pending'
        item = self.reserve(meeting_method='google_meet')
        sync_appointment(item.pk, calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertFalse(item.meet_url)
        self.assertFalse(item.notifications.exists())
        with self.assertRaises(ValidationError):
            self.reserve(email='second@example.invalid')
        calendar.status = 'success'
        sync_appointment(item.pk, calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(item.meet_url, URL)
        self.assertEqual(len(calendar.events), 2)
        self.assertEqual(item.notifications.count(), 2)
        for notice in item.notifications.all():
            self.assertIn(URL, notice.body)

    def test_failed_conference_never_confirms_or_sends_emails(self):
        calendar = MeetCalendarDouble()
        calendar.status = 'failure'
        item = self.reserve(meeting_method='google_meet')
        sync_appointment(item.pk, calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'attention')
        self.assertIn('could not be created', item.last_error)
        self.assertFalse(item.notifications.exists())

    def test_google_move_keeps_link_and_notifies_with_updated_time(self):
        calendar = MeetCalendarDouble()
        item = self.reserve(meeting_method='google_meet')
        sync_appointment(item.pk, calendar)
        event = calendar.events[item.event_ids[0]]
        event['start']['dateTime'] = (START+timedelta(hours=1)).isoformat()
        event['end']['dateTime'] = (START+timedelta(hours=1, minutes=30)).isoformat()
        event['etag'] = 'moved'
        sync_appointment(item.pk, calendar)
        item.refresh_from_db()
        self.assertEqual(item.meet_url, URL)
        self.assertEqual(item.starts_at, START+timedelta(hours=1))
        self.assertIn(URL, item.notifications.get(appointment_event='changed-1', recipient_kind='client').body)

    def test_failed_meet_reschedule_keeps_original_and_method_then_new_unique_request(self):
        calendar = MeetCalendarDouble()
        original = self.reserve(meeting_method='google_meet')
        sync_appointment(original.pk, calendar)
        replacement = self.reserve(start=START+timedelta(days=1), previous_id=original.pk)
        self.assertEqual(replacement.meeting_method, 'google_meet')
        self.assertFalse(replacement.meet_url)
        calendar.status = 'pending'
        sync_appointment(replacement.pk, calendar)
        original.refresh_from_db()
        self.assertEqual(original.state, 'confirmed')
        self.assertIn(original.event_ids[0], calendar.events)
        calendar.status = 'success'
        sync_appointment(replacement.pk, calendar)
        original.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(original.state, 'canceled')
        self.assertEqual(replacement.state, 'confirmed')
        self.assertNotEqual(expected_events(original)[0]['conferenceData'], expected_events(replacement)[0]['conferenceData'])

    def test_cancel_meet_removes_both_events(self):
        calendar = MeetCalendarDouble()
        item = self.reserve(meeting_method='google_meet')
        sync_appointment(item.pk, calendar)
        request_cancel(item.pk, email=item.email)
        sync_appointment(item.pk, calendar)
        item.refresh_from_db()
        self.assertEqual(item.state, 'canceled')
        self.assertFalse(calendar.events)

    def test_legacy_appointments_keep_unspecified_method(self):
        item = self.reserve()
        sync_appointment(item.pk, self.calendar)
        self.assertEqual(item.meeting_method, '')
        self.assertNotIn('conferenceData', expected_events(item)[0])
        self.assertIn('coordinate meeting details', item.notifications.get(recipient_kind='client').body)

    @override_settings(CONTACT_ADDRESS='123 Main St, Your City, ST 12345')
    def test_placeholder_office_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.reserve(meeting_method='in_person')
        self.assertFalse(Appointment.objects.exists())

    def test_booking_ui_shows_required_selector_address_and_instructions(self):
        self.signin()
        response = self.client.get(reverse('workqueue:book'), {'day':START.date().isoformat()})
        self.assertContains(response, 'How would you like to meet?')
        self.assertContains(response, ADDRESS)
        self.assertContains(response, 'Google Meet')
        token = response.context['form'].initial['token']
        answers = {'token': token, 'day':START.date().isoformat(), 'slot':START.isoformat(),
            'full_name':'Alex', 'phone':'555', 'purpose':'Review', 'terms_accepted':'on'}
        response = self.client.post(reverse('workqueue:book'), answers)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Appointment.objects.exists())
        answers['meeting_method'] = 'in_person'
        self.assertEqual(self.client.post(reverse('workqueue:book'), answers).status_code, 302)
        item = Appointment.objects.get()
        self.assertEqual(item.meeting_method, 'in_person')
        self.assertEqual(item.meeting_address, ADDRESS)

    def test_confirmed_client_and_staff_details_show_join_link(self):
        item = self.reserve(meeting_method='google_meet')
        sync_appointment(item.pk, MeetCalendarDouble())
        self.signin()
        response = self.client.get(reverse('workqueue:book'))
        self.assertContains(response, 'Join Google Meet')
        self.assertContains(response, URL)
        from django.template.loader import render_to_string
        html = render_to_string('workqueue/meeting_details.html', {'appointment':Appointment.objects.get()})
        self.assertIn(URL, html)


class ConferenceProviderTests(TestCase):
    def item(self):
        return Appointment(full_name='Alex', email='alex@example.invalid', phone='555', purpose='Review',
            meeting_method='google_meet', starts_at=START, ends_at=START+timedelta(minutes=30),
            reserved_until=START+timedelta(hours=1), calendar_id='primary')

    def test_insert_enables_conferences_and_reuses_stable_request_on_retry(self):
        item = self.item()
        expected = expected_events(item)[0]
        actual = {**copy.deepcopy(expected), 'etag':'event', 'conferenceData':conference()}
        provider = GoogleCalendar()
        provider.get = Mock(side_effect=[None, actual, actual])
        provider.request = Mock()
        provider.ensure(item, expected)
        provider.ensure(item, expected)
        self.assertEqual(provider.request.call_count, 1)
        call = provider.request.call_args
        self.assertEqual(call.args[0], 'POST')
        self.assertEqual(call.kwargs['params']['conferenceDataVersion'], 1)
        self.assertEqual(call.kwargs['json']['conferenceData'], expected['conferenceData'])

    def test_existing_owned_event_without_conference_patches_only_conference_with_etag(self):
        item = self.item()
        expected = expected_events(item)[0]
        initial = {key:value for key,value in expected.items() if key != 'conferenceData'}
        initial['etag'] = 'first'
        ready = {**initial, 'conferenceData':conference()}
        provider = GoogleCalendar()
        provider.get = Mock(side_effect=[initial, ready])
        provider.request = Mock()
        provider.ensure(item, expected)
        call = provider.request.call_args
        self.assertEqual(call.args[0], 'PATCH')
        self.assertEqual(set(call.kwargs['json']), {'conferenceData'})
        self.assertEqual(call.kwargs['headers'], {'If-Match':'first'})
        self.assertEqual(call.kwargs['params']['conferenceDataVersion'], 1)

    def test_pending_or_failed_conference_does_not_create_second_request(self):
        for status in ['pending','failure']:
            item = self.item()
            expected = expected_events(item)[0]
            actual = {**expected, 'conferenceData':conference(status), 'etag':'event'}
            provider = GoogleCalendar()
            provider.get = Mock(return_value=actual)
            provider.request = Mock()
            with self.assertRaises(CalendarUnavailable):
                provider.ensure(item, expected)
            provider.request.assert_not_called()

    def test_only_verified_google_video_urls_are_accepted(self):
        self.assertEqual(verified_meet_url({'conferenceData':conference()}), URL)
        for url in ['javascript:alert(1)', 'https://meet.google.com.evil.test/abc-defg-hij',
                'https://meet.google.com@evil.test/abc-defg-hij', 'http://meet.google.com/abc-defg-hij',
                URL+'?secret=value', URL+'#x']:
            with self.assertRaises(CalendarUnavailable):
                verified_meet_url({'conferenceData':conference(uri=url)})

    def test_link_from_changed_or_foreign_event_is_rejected(self):
        item = self.item()
        event = {**expected_events(item)[0], 'conferenceData':conference(), 'etag':'verified'}
        self.assertEqual(verified_meet_url(event, item), URL)
        event['extendedProperties'] = {'private': {'phdAppointment':'another-appointment'}}
        with self.assertRaises(CalendarUnavailable):
            verified_meet_url(event, item)

