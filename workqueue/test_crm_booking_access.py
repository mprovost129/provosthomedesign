"""End-to-end booking identity tests without pre-created grants or CRM records."""
import re
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .access import mint_email_token
from .booking import eligible, sync_appointment
from .models import Appointment, BookingAccess, Client as CRMClient, ClientContact, ProjectAccess, WorkItem
from .models import ConsultationRequest, NotificationDelivery
from .test_booking import CalendarDouble, NOW, START


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, BOOKING_CALENDAR_BACKEND='google',
    RECAPTCHA_ENTERPRISE_API_KEY='', RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='',
    BOOKING_ENABLED=True, WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True,
    INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com', INTAKE_OWNER_EMAIL='owner@example.invalid')
class CRMBookingAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.calendar=CalendarDouble()
        for target,value in [('django.utils.timezone.now',NOW),('workqueue.booking.calendar_provider',self.calendar)]:
            patcher=patch(target,return_value=value);patcher.start();self.addCleanup(patcher.stop)

    def verify(self,email='new@example.invalid'):
        # Requesting browser may differ from the browser opening the email.
        response=self.client.post(reverse('workqueue:book'),{'email':email})
        self.assertEqual(response.status_code,200)
        notice=NotificationDelivery.objects.filter(recipient=email.strip().lower()).latest('pk')
        raw=re.search(r'#token=([A-Za-z0-9_-]+)',notice.body)[1]
        browser=Client()
        self.assertEqual(browser.get(reverse('workqueue:confirm_access')).status_code,200)
        response=browser.post(reverse('workqueue:confirm_access'),{'token':raw})
        self.assertEqual(response.url,reverse('workqueue:book'))
        self.assertEqual(browser.session['queue_verified_email'],email.strip().lower())
        return browser,raw

    def form_data(self,browser,start=START):
        page=browser.get(reverse('workqueue:book'),{'day':start.date().isoformat()})
        self.assertTrue(page.context['permitted'])
        self.assertContains(page,'calendar-grid')
        return {'token':page.context['form'].initial['token'],'day':start.date().isoformat(),
            'slot':start.isoformat(),'full_name':'New Visitor','phone':'555','purpose':'Discuss a new home',
            'meeting_method':'phone','terms_accepted':'on'}

    def test_truly_new_email_verifies_books_and_confirms_without_client_or_project_access(self):
        browser,_=self.verify('  New@Example.Invalid  ')
        data=self.form_data(browser)
        self.assertEqual(browser.post(reverse('workqueue:book'),data).status_code,302)
        item=Appointment.objects.get()
        self.assertEqual(item.email,'new@example.invalid')
        self.assertEqual(item.state,'pending')
        self.assertEqual(NotificationDelivery.objects.filter(appointment=item).count(),0)
        sync_appointment(item.pk,self.calendar)
        item.refresh_from_db()
        self.assertEqual(item.state,'confirmed')
        self.assertEqual(NotificationDelivery.objects.filter(appointment=item).count(),2)
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(CRMClient.objects.exists())
        self.assertFalse(ClientContact.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(get_user_model().objects.exists())

    def test_existing_crm_contact_verifies_and_books_without_manual_grant(self):
        account=CRMClient.objects.create(name='Existing',normalized_name='existing')
        ClientContact.objects.create(client=account,email='new@example.invalid')
        browser,_=self.verify()
        self.assertEqual(browser.post(reverse('workqueue:book'),self.form_data(browser)).status_code,302)
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())

    def test_legacy_pending_inquiry_denial_and_archived_contact_do_not_block_verified_booking(self):
        account=CRMClient.objects.create(name='Legacy',normalized_name='legacy')
        contact=ClientContact.objects.create(client=account,email='new@example.invalid',
            archived_at=NOW,booking_requires_approval=True)
        BookingAccess.objects.create(email=contact.email,allowed=False)
        inquiry=ConsultationRequest.objects.create(email=contact.email,full_name='Legacy inquiry',phone='555',
            description='Previously waiting',state='pending',contact=contact,verified_at=NOW,
            terms_accepted_at=NOW,idempotency_key='legacy-pending')
        browser,_=self.verify()
        self.assertEqual(browser.get(reverse('workqueue:consultation')).url,reverse('workqueue:book'))
        self.assertEqual(browser.post(reverse('workqueue:book'),self.form_data(browser)).status_code,302)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.state,'pending')  # Historical state needs no production rewrite.
        contact.refresh_from_db()
        self.assertTrue(contact.booking_requires_approval)
        self.assertFalse(BookingAccess.objects.get().allowed)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_unverified_posted_email_cannot_view_calendar_or_create_appointment(self):
        browser=Client()
        with patch('workqueue.booking_views.available_days') as availability:
            page=browser.get(reverse('workqueue:book'),{'email':'new@example.invalid'})
            self.assertNotContains(page,'calendar-grid')
            result=browser.post(reverse('workqueue:book'),{'email':'new@example.invalid','day':START.date().isoformat(),
                'slot':START.isoformat(),'full_name':'Forged','phone':'555','purpose':'No verification'})
        self.assertEqual(result.status_code,200)
        availability.assert_not_called()
        self.assertFalse(Appointment.objects.exists())
        self.assertNotIn('queue_verified_email',browser.session)

    def test_expired_session_cannot_submit_previously_displayed_booking_form(self):
        browser,_=self.verify()
        data=self.form_data(browser)
        session=browser.session;session['queue_email_verified_until']=NOW.timestamp()-1;session.save()
        result=browser.post(reverse('workqueue:book'),data)
        self.assertEqual(result.status_code,400)
        self.assertContains(browser.get(reverse('workqueue:book')),'Verify your email')
        self.assertFalse(Appointment.objects.exists())

    def test_expired_invalid_and_reused_verification_links_cannot_authenticate_new_browser(self):
        record,expired=mint_email_token('new@example.invalid',destination='booking')
        record.expires_at=NOW-timedelta(seconds=1);record.save(update_fields=['expires_at'])
        _,used=self.verify()
        for raw in ('incorrect-token',expired,used):
            browser=Client()
            self.assertEqual(browser.post(reverse('workqueue:confirm_access'),{'token':raw}).status_code,400)
            self.assertNotIn('queue_verified_email',browser.session)
        self.assertFalse(Appointment.objects.exists())

    def test_double_booking_click_creates_one_appointment(self):
        browser,_=self.verify()
        data=self.form_data(browser)
        for _ in range(2):
            self.assertEqual(browser.post(reverse('workqueue:book'),data).status_code,302)
        self.assertEqual(Appointment.objects.count(),1)

    def test_two_new_verified_emails_cannot_book_same_slot_from_stale_forms(self):
        first,_=self.verify('first@example.invalid')
        second,_=self.verify('second@example.invalid')
        first_data=self.form_data(first);second_data=self.form_data(second)
        self.assertEqual(first.post(reverse('workqueue:book'),first_data).status_code,302)
        self.assertEqual(second.post(reverse('workqueue:book'),second_data).status_code,400)
        self.assertEqual(Appointment.objects.count(),1)

    def test_calendar_conflict_after_selection_rejects_booking(self):
        browser,_=self.verify()
        data=self.form_data(browser)
        self.calendar.external=[(START,START+timedelta(hours=1))]
        self.assertEqual(browser.post(reverse('workqueue:book'),data).status_code,400)
        self.assertFalse(Appointment.objects.exists())

    def test_posted_email_cannot_replace_verified_identity_or_attach_project(self):
        browser,_=self.verify()
        data=self.form_data(browser);data['email']='victim@example.invalid'
        self.assertEqual(browser.post(reverse('workqueue:book'),data).status_code,302)
        self.assertEqual(Appointment.objects.get().email,'new@example.invalid')
        self.assertFalse(ProjectAccess.objects.exists())

    def test_email_alias_requires_its_own_verification_but_not_a_crm_entry(self):
        browser,_=self.verify('new+consult@example.invalid')
        self.assertEqual(browser.post(reverse('workqueue:book'),self.form_data(browser)).status_code,302)
        self.assertEqual(Appointment.objects.get().email,'new+consult@example.invalid')
        self.assertTrue(eligible('new+consult@example.invalid'))
        self.assertFalse(eligible(''))
        self.assertFalse(eligible('invalid'))
