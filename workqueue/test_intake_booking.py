from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token
from .drafts import partial_answers
from .intake_booking import SESSION_KEY
from .models import Appointment, EmailAccessToken, NotificationDelivery, ProjectAccess, Submission, WorkItem
from .test_client_intake import new_answers


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, WORK_INTAKE_ENABLED=True,
    WORK_QUEUE_ENABLED=True, BOOKING_ENABLED=True, BOOKING_CALENDAR_BACKEND='google',
    INTAKE_OWNER_EMAIL='owner@example.invalid', INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com',
    RECAPTCHA_ENTERPRISE_API_KEY='', RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class IntakeBookingTests(TestCase):
    def setUp(self):
        cache.clear()
        page = self.client.get(reverse('workqueue:submit'))
        self.token = page.context['form'].initial['intake_token']

    def submit(self, **changes):
        return self.client.post(reverse('workqueue:submit'), new_answers(intake_token=self.token, **changes))

    def verify(self, email='alex@example.invalid'):
        _, token = mint_email_token(email, destination='booking')
        return self.client.post(reverse('workqueue:confirm_access'), {'token': token})

    def test_optional_choice_is_available_on_both_paths(self):
        for query in ({}, {'kind': 'update'}):
            page = self.client.get(reverse('workqueue:submit'), query)
            self.assertContains(page, 'name="request_appointment"')
            self.assertContains(page, 'Request an appointment')
            self.assertFalse(page.context['form'].fields['request_appointment'].required)

    def test_selected_new_request_saves_before_booking_without_reserving_or_extra_email(self):
        response = self.submit(request_appointment='on')
        self.assertRedirects(response, reverse('workqueue:book'), fetch_redirect_response=False)
        item = WorkItem.objects.get()
        self.assertEqual(item.submission_position, 1)
        self.assertEqual(Submission.objects.get().answers['Request an appointment'], 'Yes')
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        for kind in ['client', 'owner']:
            notice = NotificationDelivery.objects.get(recipient_kind=kind)
            self.assertIn('Request an appointment', notice.body)
        receipt = NotificationDelivery.objects.get(recipient_kind='client')
        self.assertIn('no meeting is reserved yet', receipt.body)
        self.assertIn('/book-appointment/', receipt.body)
        self.assertNotIn('queue_verified_email', self.client.session)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_unselected_request_keeps_original_confirmation(self):
        response = self.submit()
        self.assertRedirects(response, reverse('workqueue:submitted', args=[WorkItem.objects.get().pk]), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_email_signin_step_keeps_upload_confirmation(self):
        self.submit(request_appointment='on')
        page = self.client.post(reverse('workqueue:book'), {'email': 'alex@example.invalid'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Your work was submitted successfully.')
        self.assertContains(page, 'Check your email for a secure sign-in link')
        self.assertEqual(EmailAccessToken.objects.filter(destination='booking').count(), 1)
        self.assertEqual(WorkItem.objects.count(), 1)

    @patch('workqueue.booking_views.available_days')
    def test_anonymous_booking_retains_receipt_and_prefills_email_without_calendar_or_access(self, days):
        self.submit(request_appointment='on')
        page = self.client.get(reverse('workqueue:book'))
        self.assertContains(page, 'Your work was submitted successfully.')
        self.assertContains(page, WorkItem.objects.get().reference)
        self.assertContains(page, 'View your submission confirmation')
        self.assertContains(page, 'No appointment is reserved yet')
        self.assertEqual(page.context['access_form'].initial['email'], 'alex@example.invalid')
        self.assertFalse(page.context['permitted'])
        days.assert_not_called()
        self.assertFalse(ProjectAccess.objects.exists())

    @patch('workqueue.booking_views.available_days', return_value={})
    def test_verified_potential_client_gets_saved_contact_and_authorized_project(self, days):
        self.submit(request_appointment='on')
        self.verify()
        page = self.client.get(reverse('workqueue:book'))
        form = page.context['form']
        item = WorkItem.objects.get()
        self.assertTrue(page.context['permitted'])
        self.assertEqual(form.initial['full_name'], item.contact_full_name)
        self.assertEqual(form.initial['phone'], item.contact_phone)
        self.assertEqual(form.initial['project'], item.project_id)
        self.assertIn(item.reference, form.initial['purpose'])
        self.assertContains(page, 'Your work was submitted successfully.')
        self.assertFalse(Appointment.objects.exists())
        days.assert_called_once()

    def test_update_can_choose_booking_without_requiring_billing_details_or_disclosing_project(self):
        response = self.client.post(reverse('workqueue:submit'), {'terms_accepted': 'on', 'kind': 'update',
            'contact_full_name': 'Alex Example', 'company': 'Homeowner', 'contact_email': 'alex@example.invalid',
            'contact_phone': '5085550100', 'intake_token': self.token, 'project_reference': 'PHD-12345',
            'update_description': 'Updated drawings', 'categories': ['Revision request'], 'request_appointment': 'on'})
        self.assertRedirects(response, reverse('workqueue:book'), fetch_redirect_response=False)
        self.assertIsNone(WorkItem.objects.get().project_id)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_invalid_submission_keeps_choice_without_booking_handoff(self):
        page = self.submit(request_appointment='on', contact_phone='')
        self.assertEqual(page.status_code, 400)
        self.assertTrue(page.context['form']['request_appointment'].value())
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_selected_retry_uses_original_choice_and_does_not_duplicate(self):
        self.submit(request_appointment='on')
        response = self.submit(request_appointment='', new_description='Retry')
        self.assertRedirects(response, reverse('workqueue:book'), fetch_redirect_response=False)
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(Submission.objects.get().answers['Request an appointment'], 'Yes')

    def test_unselected_retry_cannot_change_accepted_choice(self):
        self.submit()
        response = self.submit(request_appointment='on')
        self.assertRedirects(response, reverse('workqueue:submitted', args=[WorkItem.objects.get().pk]), fetch_redirect_response=False)
        self.assertNotIn('Request an appointment', Submission.objects.get().answers)
        self.assertNotIn(SESSION_KEY, self.client.session)

    @override_settings(BOOKING_ENABLED=False)
    def test_disabled_booking_preserves_successful_upload(self):
        self.assertNotContains(self.client.get(reverse('workqueue:submit')), 'name="request_appointment"')
        response = self.submit(request_appointment='on')
        self.assertRedirects(response, reverse('workqueue:submitted', args=[WorkItem.objects.get().pk]), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_other_browser_cannot_see_submission_context(self):
        self.submit(request_appointment='on')
        other = Client()
        page = other.get(reverse('workqueue:book'))
        self.assertNotContains(page, WorkItem.objects.get().reference)
        self.assertNotContains(page, 'alex@example.invalid')
        session = other.session
        session[SESSION_KEY] = self.client.session[SESSION_KEY]
        session.save()
        page = other.get(reverse('workqueue:book'))
        self.assertIsNone(page.context['booking_upload'])

    def test_expired_or_deleted_submission_context_is_ignored(self):
        self.submit(request_appointment='on')
        session = self.client.session
        session[SESSION_KEY] = {**session[SESSION_KEY], 'expires': (timezone.now()-timedelta(seconds=1)).timestamp()}
        session.save()
        self.assertIsNone(self.client.get(reverse('workqueue:book')).context['booking_upload'])
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.submit(request_appointment='on')
        WorkItem.objects.update(deleted_at=timezone.now())
        self.assertIsNone(self.client.get(reverse('workqueue:book')).context['booking_upload'])

    @patch('workqueue.booking_views.available_days')
    def test_different_verified_identity_must_verify_submission_email(self, days):
        self.verify('other@example.invalid')
        self.submit(request_appointment='on')
        page = self.client.get(reverse('workqueue:book'))
        self.assertTrue(page.context['booking_identity_mismatch'])
        self.assertEqual(page.context['access_form'].initial['email'], 'alex@example.invalid')
        self.assertFalse(page.context['permitted'])
        self.assertEqual(self.client.session['queue_verified_email'], 'other@example.invalid')
        days.assert_not_called()

    def test_clear_context_does_not_delete_submission_or_email_verification(self):
        self.verify()
        self.submit(request_appointment='on')
        response = self.client.post(reverse('workqueue:book'), {'action': 'clear_submission'})
        self.assertRedirects(response, reverse('workqueue:book'), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.session['queue_verified_email'], 'alex@example.invalid')
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_context_clear_remains_csrf_protected(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('workqueue:book'), {'action': 'clear_submission'}).status_code, 403)

    def test_draft_saves_choice_without_becoming_a_booking(self):
        self.assertEqual(partial_answers({'request_appointment': True}), {'request_appointment': True})
        self.assertFalse(Appointment.objects.exists())

    @patch('workqueue.booking_views.available_days', return_value={})
    @patch('workqueue.booking_views.available_times', return_value=[])
    def test_successful_reservation_clears_upload_not_yet_booked_banner(self, slots, days):
        self.submit(request_appointment='on')
        self.verify()
        page = self.client.get(reverse('workqueue:book'))
        with patch('workqueue.booking_views.AppointmentForm') as form_class, patch('workqueue.booking_views.reserve_appointment') as reserve:
            reserve.return_value = (None, True)
            form = form_class.return_value
            form.is_valid.return_value = True
            form.cleaned_data = {'token': page.context['form'].initial['token'], 'slot': timezone.now().isoformat(),
                'full_name': 'Alex Example', 'phone': '5085550100', 'purpose': 'Discuss project', 'project': None, 'meeting_method': 'phone'}
            response = self.client.post(reverse('workqueue:book'), {'day': timezone.now().date().isoformat()})
        self.assertEqual(response.status_code, 302)
        reserve.assert_called_once()
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(WorkItem.objects.count(), 1)
