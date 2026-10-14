import re
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client as BrowserClient, override_settings
from django.urls import reverse
from django.utils import timezone
from .access import consume_email_token,mint_email_token
from .booking import eligible,set_booking_access
from .consultations import submit_consultation,verify_consultation,decide_consultation,resend_verification
from .consultation_views import ConsultationForm
from .models import Client,ClientContact,ConsultationRequest,NotificationDelivery,BookingAccess,WorkItem,ProjectAccess


@override_settings(DEBUG=True,INTAKE_LOCAL_DEVELOPMENT=True,RECAPTCHA_ENTERPRISE_API_KEY='',RECAPTCHA_SECRET_KEY='',
    RECAPTCHA_PRIVATE_KEY='',BOOKING_ENABLED=True,WORK_INTAKE_ENABLED=True,WORK_QUEUE_ENABLED=True,
    INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com',INTAKE_OWNER_EMAIL='owner@example.invalid')
class ConsultationTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.owner=get_user_model().objects.create_superuser('consult-owner','owner@example.invalid','local-test-only')

    def submit(self,**changes):
        values=dict(email='prospect@example.invalid',full_name='New Prospect',phone='555',description='A new home design',
            key='consult-'+uuid.uuid4().hex,terms_url='/terms/')
        values.update(changes)
        return submit_consultation(**values)

    def raw(self,item):
        return re.search(r'#token=([A-Za-z0-9_-]+)',item.verification_delivery.body)[1]

    def signin(self,email):
        session=self.client.session
        session['queue_verified_email']=email
        session['queue_email_verified_until']=(timezone.now()+timedelta(days=7)).timestamp()
        session.save()

    def test_unverified_request_only_queues_verification_not_crm_or_work(self):
        item=self.submit()
        self.assertEqual(item.state,'unverified')
        self.assertEqual(NotificationDelivery.objects.count(),1)
        self.assertFalse(Client.objects.exists())
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(eligible(item.email))
        self.assertEqual(item.terms_url,'/terms/')
        self.assertIsNotNone(item.terms_accepted_at)

    def test_verification_creates_inquiry_but_blocks_automatic_booking(self):
        item=self.submit()
        consume_email_token(self.raw(item))
        item.refresh_from_db()
        self.assertEqual(item.state,'pending')
        self.assertEqual(item.contact.client.stage,'inquiry')
        self.assertTrue(item.contact.booking_requires_approval)
        self.assertFalse(eligible(item.email))
        self.assertFalse(BookingAccess.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertFalse(WorkItem.objects.exists())
        self.assertEqual(item.owner_delivery.recipient,'owner@example.invalid')
        self.assertIn('A new home design',item.owner_delivery.body)

    def test_verification_callback_returns_to_consultation_pending_page(self):
        item=self.submit()
        response=self.client.post(reverse('workqueue:confirm_access'),{'token':self.raw(item)})
        self.assertRedirects(response,reverse('workqueue:consultation'))
        self.assertContains(self.client.get(reverse('workqueue:consultation')),'awaiting Mike’s review')

    def test_resend_is_not_offered_after_the_request_has_been_verified(self):
        item=self.submit()
        session=self.client.session
        session['consultation_recent_id']=str(item.pk);session['consultation_recent_email']=item.email
        session.save()
        self.client.post(reverse('workqueue:confirm_access'),{'token':self.raw(item)})
        self.assertNotContains(self.client.get(reverse('workqueue:consultation')),'Resend verification email')

    def test_existing_verified_session_can_submit_without_new_verification_mail(self):
        item=self.submit(verified=True)
        self.assertEqual(item.state,'pending')
        self.assertIsNone(item.verification_delivery)
        self.assertEqual(NotificationDelivery.objects.count(),2)
        self.assertFalse(eligible(item.email))

    def test_approval_enables_booking_sends_single_client_invitation(self):
        item=self.submit(verified=True)
        decide_consultation(item.pk,True,self.owner)
        item.refresh_from_db();item.contact.refresh_from_db()
        self.assertEqual(item.state,'approved')
        self.assertFalse(item.contact.booking_requires_approval)
        self.assertTrue(eligible(item.email))
        self.assertIn('#token=',item.decision_delivery.body)
        before=NotificationDelivery.objects.count()
        decide_consultation(item.pk,True,self.owner)
        self.assertEqual(NotificationDelivery.objects.count(),before)
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertFalse(WorkItem.objects.exists())

    def test_approval_link_returns_to_booking(self):
        item=self.submit(verified=True)
        decide_consultation(item.pk,True,self.owner)
        item.refresh_from_db()
        raw=re.search(r'#token=([A-Za-z0-9_-]+)',item.decision_delivery.body)[1]
        with patch('workqueue.booking.available_days',return_value={}):
            response=self.client.post(reverse('workqueue:confirm_access'),{'token':raw})
        self.assertEqual(response.url,reverse('workqueue:book'))

    def test_decline_does_not_enable_booking(self):
        item=self.submit(verified=True)
        decide_consultation(item.pk,False,self.owner)
        item.refresh_from_db()
        self.assertEqual(item.state,'declined')
        self.assertFalse(eligible(item.email))
        self.assertIn('unable to offer',item.decision_delivery.body)
        self.assertFalse(BookingAccess.objects.exists())
        with self.assertRaises(ValidationError):decide_consultation(item.pk,True,self.owner)

    def test_unverified_request_cannot_be_approved_and_non_staff_cannot_decide(self):
        item=self.submit()
        with self.assertRaises(ValidationError):decide_consultation(item.pk,True,self.owner)
        other=get_user_model().objects.create_user('other')
        with self.assertRaises(PermissionDenied):decide_consultation(item.pk,True,other)
        self.assertFalse(BookingAccess.objects.exists())

    def test_existing_crm_email_is_reused_without_overwriting_contact(self):
        account=Client.objects.create(name='Existing',normalized_name='existing')
        contact=ClientContact.objects.create(client=account,email='prospect@example.invalid',full_name='Original',phone='original')
        item=self.submit()
        consume_email_token(self.raw(item));item.refresh_from_db();contact.refresh_from_db()
        self.assertEqual(item.contact_id,contact.pk)
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(contact.full_name,'Original')
        self.assertEqual(item.state,'approved')
        self.assertTrue(eligible(item.email))

    def test_explicit_block_is_not_undone_by_consultation_verification(self):
        set_booking_access('prospect@example.invalid',False,self.owner)
        item=self.submit(verified=True)
        self.assertEqual(item.state,'pending')
        self.assertFalse(eligible(item.email))
        self.assertFalse(BookingAccess.objects.get().allowed)

    def test_retries_and_multiple_requests_deduplicate_pending_email(self):
        first=self.submit(key='same-key')
        self.assertEqual(self.submit(key='same-key').pk,first.pk)
        self.assertEqual(self.submit(key='different-key').pk,first.pk)
        self.assertEqual(ConsultationRequest.objects.count(),1)
        self.assertEqual(NotificationDelivery.objects.count(),1)
        consume_email_token(self.raw(first))
        before=NotificationDelivery.objects.count()
        verify_consultation(first.pk.hex,first.email)
        self.assertEqual(NotificationDelivery.objects.count(),before)
        with self.assertRaises(ValidationError):consume_email_token(self.raw(first))

    def test_verification_must_match_request_email(self):
        item=self.submit()
        with self.assertRaises(ValidationError):verify_consultation(item.pk.hex,'other@example.invalid')
        self.assertFalse(Client.objects.exists())

    def test_contact_email_change_requires_review_before_approval(self):
        item=self.submit(verified=True)
        item.contact.email='changed@example.invalid';item.contact.save()
        with self.assertRaises(ValidationError):decide_consultation(item.pk,True,self.owner)
        self.assertFalse(BookingAccess.objects.exists())

    def test_archived_contact_can_be_declined_without_enabling_booking(self):
        item=self.submit(verified=True)
        item.contact.archived_at=timezone.now();item.contact.save()
        decide_consultation(item.pk,False,self.owner)
        item.refresh_from_db()
        self.assertEqual(item.state,'declined')
        self.assertFalse(eligible(item.email))

    def test_resend_mints_new_link_without_duplicate_owner_notice(self):
        item=self.submit()
        old=self.raw(item)
        item=resend_verification(item.pk,item.email)
        self.assertNotEqual(old,self.raw(item))
        consume_email_token(self.raw(item))
        consume_email_token(old)
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(NotificationDelivery.objects.filter(recipient_kind='owner').count(),1)

    def test_request_is_linked_from_booking_and_requires_terms(self):
        response=self.client.get(reverse('workqueue:book'))
        self.assertContains(response,'Request an initial consultation')
        form=ConsultationForm({'full_name':'New','email':'new@example.invalid','phone':'555','description':'New home','token':'x'})
        self.assertFalse(form.is_valid());self.assertIn('terms_accepted',form.errors)

    def test_verification_page_does_not_assume_a_prospect_has_an_existing_project(self):
        response=self.client.get(reverse('workqueue:confirm_access'))
        self.assertContains(response,'Confirm your email')
        self.assertContains(response,'Confirm and continue')
        self.assertNotContains(response,'current place in line')

    def test_public_form_saves_and_unknown_session_cannot_see_another_description(self):
        response=self.client.get(reverse('workqueue:consultation'))
        token=response.context['form'].initial['token']
        response=self.client.post(reverse('workqueue:consultation'),{'token':token,'full_name':'New','email':'prospect@example.invalid',
            'phone':'555','description':'PRIVATE PROJECT DESCRIPTION','terms_accepted':'on'})
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(reverse('workqueue:consultation')),'Check your email')
        self.assertNotContains(BrowserClient().get(reverse('workqueue:consultation')),'PRIVATE PROJECT DESCRIPTION')
        self.assertFalse(Client.objects.exists())

    def test_staff_queue_approve_and_repeated_post_sends_once(self):
        item=self.submit(verified=True)
        self.client.force_login(self.owner)
        for _ in range(2):
            response=self.client.post(reverse('workqueue:queue'),{'action':'consultation_approve','consultation_id':str(item.pk)})
            self.assertEqual(response.status_code,302)
        item.refresh_from_db()
        self.assertTrue(eligible(item.email))
        self.assertEqual(NotificationDelivery.objects.filter(subject='Consultation request approved').count(),1)

    @override_settings(DEBUG=False,INTAKE_LOCAL_DEVELOPMENT=False,RECAPTCHA_SITE_KEY='test-key',RECAPTCHA_SECRET_KEY='test-secret')
    def test_production_form_rejects_missing_recaptcha_without_creating_inquiry(self):
        token=self.client.get(reverse('workqueue:consultation')).context['form'].initial['token']
        response=self.client.post(reverse('workqueue:consultation'),{'token':token,'full_name':'New','email':'prospect@example.invalid',
            'phone':'555','description':'New house','terms_accepted':'on'})
        self.assertEqual(response.status_code,400)
        self.assertFalse(ConsultationRequest.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    @override_settings(DEBUG=False,INTAKE_LOCAL_DEVELOPMENT=False,RECAPTCHA_SITE_KEY='test-key',RECAPTCHA_SECRET_KEY='test-secret')
    def test_production_valid_recaptcha_uses_consultation_action_and_keeps_request_unverified(self):
        token=self.client.get(reverse('workqueue:consultation')).context['form'].initial['token']
        with patch('workqueue.client_security.verify_recaptcha_v3',return_value=(True,{})) as verify:
            response=self.client.post(reverse('workqueue:consultation'),{'token':token,'full_name':'New','email':'prospect@example.invalid',
                'phone':'555','description':'New house','terms_accepted':'on','recaptcha_token':'test-only'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(verify.call_args.kwargs['expected_action'],'work_consultation')
        self.assertEqual(ConsultationRequest.objects.get().state,'unverified')
        self.assertFalse(Client.objects.exists())

    def test_consultation_post_requires_csrf(self):
        browser=BrowserClient(enforce_csrf_checks=True)
        token=browser.get(reverse('workqueue:consultation')).context['form'].initial['token']
        response=browser.post(reverse('workqueue:consultation'),{'token':token,'full_name':'New','email':'prospect@example.invalid',
            'phone':'555','description':'New house','terms_accepted':'on'})
        self.assertEqual(response.status_code,403)
        self.assertFalse(ConsultationRequest.objects.exists())
