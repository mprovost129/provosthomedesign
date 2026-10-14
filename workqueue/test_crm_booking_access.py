from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_booking as baseline
from .access import consume_email_token, mint_email_token
from .booking import eligible, set_booking_access, sync_appointment, request_cancel
from .models import Appointment, BookingAccess, Client as CRMClient, ClientContact, ProjectAccess
from .test_booking import START, NOW


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, RECAPTCHA_ENTERPRISE_API_KEY='',
    RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='', BOOKING_ENABLED=True, WORK_INTAKE_ENABLED=True,
    WORK_QUEUE_ENABLED=True, BOOKING_HORIZON_DAYS=60, GOOGLE_CALENDAR_ID='primary',
    INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com', INTAKE_OWNER_EMAIL='owner@example.invalid')
class CRMBookingAccessTests(TestCase):
    reserve = baseline.BookingTests.reserve
    signin = baseline.BookingTests.signin

    def setUp(self):
        baseline.BookingTests.setUp(self)
        BookingAccess.objects.all().delete()
        self.account = CRMClient.objects.create(name='Existing homeowner', normalized_name='existing homeowner')
        self.contact = ClientContact.objects.create(client=self.account, email='alex@example.invalid', full_name='Alex Example')

    def request_booking(self):
        response = self.client.get(reverse('workqueue:book'), {'day':START.date().isoformat()})
        return self.client.post(reverse('workqueue:book'), {
            'token':response.context['form'].initial['token'], 'day':START.date().isoformat(),
            'slot':START.isoformat(), 'full_name':'Alex Example', 'phone':'555', 'purpose':'Review the plan',
            'meeting_method':'phone', 'terms_accepted':'on'})

    def test_current_crm_email_is_eligible_without_any_work_or_manual_grant(self):
        self.assertTrue(eligible('  ALEX@EXAMPLE.INVALID  '))
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())

    def test_unknown_empty_and_alias_emails_stay_ineligible(self):
        for email in ['', 'unknown@example.invalid', 'alex+other@example.invalid']:
            self.assertFalse(eligible(email))

    def test_no_calendar_or_booking_form_before_verification(self):
        response = self.client.get(reverse('workqueue:book'))
        self.assertContains(response, 'Email me a sign-in link')
        self.assertNotContains(response, 'Choose a date')
        self.assertFalse(BookingAccess.objects.exists())

    def test_verified_existing_session_gets_calendar_immediately(self):
        self.signin()
        response = self.client.get(reverse('workqueue:book'))
        self.assertTrue(response.context['permitted'])
        self.assertContains(response, 'Choose a date')
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())

    def test_verified_crm_client_can_book_without_persisting_manual_grant(self):
        self.signin()
        with patch('workqueue.booking.calendar_provider',return_value=self.calendar):
            response = self.request_booking()
        self.assertEqual(response.status_code,302)
        self.assertEqual(Appointment.objects.get().email,'alex@example.invalid')
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())

    def test_expired_verified_session_requires_email_verification_again(self):
        self.signin()
        session=self.client.session
        session['queue_email_verified_until']=NOW.timestamp()-1
        session.save()
        response=self.client.get(reverse('workqueue:book'))
        self.assertContains(response,'Email me a sign-in link')
        self.assertNotContains(response,'Choose a date')

    def test_explicit_booking_block_wins_over_crm_and_repeated_verification(self):
        set_booking_access('alex@example.invalid',False,None)
        _,raw=mint_email_token('alex@example.invalid',destination='booking')
        consume_email_token(raw)
        self.signin()
        self.assertFalse(eligible('alex@example.invalid'))
        self.assertFalse(BookingAccess.objects.get().allowed)
        response=self.client.get(reverse('workqueue:book'))
        self.assertFalse(response.context['permitted'])
        self.assertContains(response,'Booking is not enabled for this email')

    def test_block_at_reservation_time_rejects_a_previously_displayed_form(self):
        self.signin()
        response=self.client.get(reverse('workqueue:book'),{'day':START.date().isoformat()})
        token=response.context['form'].initial['token']
        set_booking_access('alex@example.invalid',False,None)
        response=self.client.post(reverse('workqueue:book'),{'token':token,'day':START.date().isoformat(),
            'slot':START.isoformat(),'full_name':'Alex','phone':'555','purpose':'Review','meeting_method':'phone','terms_accepted':'on'})
        self.assertEqual(response.status_code,403)
        self.assertFalse(Appointment.objects.exists())

    def test_manual_allow_works_for_non_crm_email_and_can_be_revoked(self):
        set_booking_access('other@example.invalid',True,None)
        self.assertTrue(eligible('other@example.invalid'))
        set_booking_access('other@example.invalid',False,None)
        self.assertFalse(eligible('other@example.invalid'))

    def test_archived_contact_and_merged_source_are_not_auto_eligible(self):
        self.contact.archived_at=timezone.now()
        self.contact.save(update_fields=['archived_at'])
        self.assertFalse(eligible('alex@example.invalid'))
        self.contact.archived_at=None
        self.contact.save(update_fields=['archived_at'])
        target=CRMClient.objects.create(name='Merged target',normalized_name='merged target')
        self.account.merged_into=target
        self.account.save(update_fields=['merged_into'])
        self.assertFalse(eligible('alex@example.invalid'))
        self.contact.client=target
        self.contact.save(update_fields=['client'])
        self.assertTrue(eligible('alex@example.invalid'))

    def test_email_correction_removes_old_and_enables_new_email_without_grants(self):
        self.contact.email='correct@example.invalid'
        self.contact.save(update_fields=['email'])
        self.assertFalse(eligible('alex@example.invalid'))
        self.assertTrue(eligible('correct@example.invalid'))
        self.assertFalse(BookingAccess.objects.exists())

    def test_multiple_contacts_with_same_email_do_not_duplicate_access(self):
        other=CRMClient.objects.create(name='Other company',normalized_name='other company',kind='company')
        ClientContact.objects.create(client=other,email='alex@example.invalid')
        self.assertTrue(eligible('alex@example.invalid'))
        self.assertFalse(BookingAccess.objects.exists())

    def test_booking_does_not_grant_private_project_access(self):
        _,raw=mint_email_token('alex@example.invalid',destination='booking')
        consume_email_token(raw)
        self.signin()
        response=self.client.get(reverse('workqueue:book'),{'day':START.date().isoformat()})
        self.assertEqual(response.context['form'].fields['project'].queryset.count(),0)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_disabled_client_can_still_cancel_existing_booking(self):
        # Create through the same reservation service, then remove the test helper's explicit grant.
        item=self.reserve()
        BookingAccess.objects.all().delete()
        sync_appointment(item.pk,self.calendar)
        set_booking_access(item.email,False,None)
        self.assertTrue(request_cancel(item.pk,email=item.email).cancel_requested)

    def test_imported_contact_with_mixed_case_stored_email_matches(self):
        self.contact.email='Alex@Example.Invalid'
        self.contact.save(update_fields=['email'])
        self.assertTrue(eligible('alex@example.invalid'))
