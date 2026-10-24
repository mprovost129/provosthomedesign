import copy
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail,signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,close_old_connections
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .test_native_invoices import InvoiceFixtures
from .models import NotificationDelivery
from .invoice_delivery_models import NativeInvoiceDelivery
from .invoice_delivery import queue_invoice_test,open_delivery,revoke_delivery,cancel_ineligible_invoice_notice
from .invoice_delivery_views import SALT
from .native_invoices import issue_test_invoice
from .services import EditConflict,create_work
from .tests import work_data
from .notifications import deliver_pending


class DeliveryFixtures(InvoiceFixtures):
    def seed_delivery(self):
        self.seed_billing();self.billing_plan=self.plan();self.bill=self.invoice(self.billing_plan)
        self.contact=self.item.client_contact
        NotificationDelivery.objects.update(state='canceled',body='',html_body='')

    def queue_copy(self,**changes):
        self.bill.refresh_from_db()
        args=dict(invoice_id=self.bill.pk,version=self.bill.version,contact_id=self.contact.pk,contact_email=self.contact.email,
            contact_name=self.contact.full_name or self.account.name,subject='Your project invoice',message='Review this test only.',
            owner_email='owner@example.invalid',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return queue_invoice_test(**args)

    def raw(self,delivery):
        return re.search(r'#token=([A-Za-z0-9_-]+)',delivery.notification.body).group(1)


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False,INTAKE_PUBLIC_BASE_URL='https://testserver',
    INTAKE_OWNER_EMAIL='owner@example.invalid',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',RATELIMIT_ENABLE=False)
class InvoiceDeliveryTests(DeliveryFixtures,TestCase):
    def setUp(self):
        self.seed_delivery();self.client.force_login(self.actor)

    def test_owner_only_queue_frozen_payload_and_test_subject(self):
        delivery=self.queue_copy()
        self.assertEqual(delivery.notification.recipient,'owner@example.invalid')
        self.assertEqual(delivery.notification.recipient_kind,'owner')
        self.assertEqual(delivery.notification.to_recipients,[])
        self.assertIn('[TEST ONLY] TEST-INV-00001',delivery.subject)
        self.assertIn('NOT A REQUEST FOR PAYMENT',delivery.notification.body)
        self.assertIn('View private test invoice',delivery.notification.html_body)
        self.assertEqual(delivery.digest.__len__(),64)
        self.assertNotIn(self.raw(delivery),str(delivery.snapshot))
        self.assertEqual(delivery.snapshot['total'],'100.00')
        self.assertEqual(delivery.intended_email,self.contact.email)

    def test_retry_is_idempotent_but_cannot_change_send_details(self):
        token=uuid.uuid4();delivery=self.queue_copy(token=token)
        self.assertEqual(self.queue_copy(token=token,version=1).pk,delivery.pk)
        with self.assertRaises(ValidationError):self.queue_copy(token=token,version=1,message='Different')
        self.assertEqual(NativeInvoiceDelivery.objects.count(),1)
        with self.assertRaises(ValidationError):self.queue_copy()

    def test_client_contact_and_owner_are_checked_again(self):
        other=create_work(data=work_data(contact_email='other@example.invalid',company='Other Company'),actor=self.actor)[0]
        with self.assertRaises(ValidationError):self.queue_copy(contact_id=other.client_contact_id)
        with self.assertRaises(ValidationError):self.queue_copy(owner_email='client@example.invalid')
        with self.assertRaises(ValidationError):self.queue_copy(contact_email='stale@example.invalid')
        with self.assertRaises(ValidationError):self.queue_copy(contact_name='Stale name')
        self.contact.archived_at=timezone.now();self.contact.save()
        with self.assertRaises(ValidationError):self.queue_copy()
        self.assertFalse(NativeInvoiceDelivery.objects.exists())

    def test_permission_and_stale_version(self):
        with self.assertRaises(PermissionDenied):self.queue_copy(actor=None)
        self.entry(self.bill)
        with self.assertRaises(EditConflict):self.queue_copy(version=1)
        with self.assertRaises(ValidationError):self.queue_copy(subject='Header\ninjection')
        with override_settings(INTAKE_OWNER_EMAIL=''):
            with self.assertRaises(ValidationError):self.queue_copy()

    def test_pending_unknown_or_sending_blocks_duplicate_mail(self):
        delivery=self.queue_copy()
        for state in ['pending','unknown','sending']:
            delivery.notification.state=state;delivery.notification.save()
            with self.subTest(state=state),self.assertRaises(ValidationError):self.queue_copy()

    def test_explicit_resend_rotates_old_link_and_keeps_history(self):
        first=self.queue_copy();raw=self.raw(first)
        first.notification.state='sent';first.notification.save()
        second=self.queue_copy()
        self.assertIsNone(open_delivery(raw))
        self.assertIsNotNone(open_delivery(self.raw(second)))
        first.refresh_from_db();self.assertTrue(first.revoked_at)
        self.assertEqual(NativeInvoiceDelivery.objects.count(),2)

    def test_expiry_revoke_and_void_stop_access_and_pending_delivery(self):
        delivery=self.queue_copy();raw=self.raw(delivery)
        delivery.expires_at=timezone.now()-timedelta(seconds=1);delivery.save()
        self.assertIsNone(open_delivery(raw));self.assertTrue(cancel_ineligible_invoice_notice(delivery.notification))
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'canceled')
        fresh=self.queue_copy()
        revoke_delivery(delivery_id=fresh.pk,actor=self.actor)
        fresh.refresh_from_db();fresh.notification.refresh_from_db()
        self.assertTrue(fresh.revoked_at);self.assertEqual(fresh.notification.state,'canceled')
        self.assertEqual(fresh.notification.body,'')
        final=self.queue_copy();raw=self.raw(final)
        self.void(self.bill)
        self.assertIsNone(open_delivery(raw));self.assertTrue(cancel_ineligible_invoice_notice(final.notification))

    def test_worker_cancels_owner_setting_or_recipient_changes(self):
        delivery=self.queue_copy()
        with override_settings(INTAKE_OWNER_EMAIL='new-owner@example.invalid'):
            self.assertTrue(cancel_ineligible_invoice_notice(delivery.notification))
        for field,value in [('recipient','unapproved@example.invalid'),('to_recipients',['bad@example.invalid']),
            ('cc_recipients',['bad@example.invalid']),('bcc_recipients',['bad@example.invalid'])]:
            delivery=self.queue_copy();setattr(delivery.notification,field,value);delivery.notification.save()
            self.assertTrue(cancel_ineligible_invoice_notice(delivery.notification))

    def test_worker_sends_one_owner_message_and_scrubs_secret(self):
        delivery=self.queue_copy(message='A & B <script>bad()</script>')
        raw=self.raw(delivery)
        deliver_pending(limit=20)
        delivery.notification.refresh_from_db()
        messages=[message for message in mail.outbox if delivery.invoice.number in message.subject]
        self.assertEqual(len(messages),1);message=messages[0]
        self.assertEqual(message.to,['owner@example.invalid']);self.assertEqual(message.cc,[]);self.assertEqual(message.bcc,[])
        self.assertIn('A & B',message.body)
        self.assertNotIn('<script>bad()</script>',message.alternatives[0].content)
        self.assertEqual(delivery.notification.state,'sent');self.assertEqual(delivery.notification.body,'');self.assertEqual(delivery.notification.html_body,'')
        self.assertIsNotNone(open_delivery(raw))

    def test_private_access_needs_secret_and_is_bound_to_one_delivery(self):
        delivery=self.queue_copy();browser=Browser()
        url=reverse('workqueue:invoice_test_public',args=[delivery.pk])
        self.assertEqual(browser.get(url).status_code,403)
        response=browser.post(reverse('workqueue:invoice_test_access'),{'token':self.raw(delivery)})
        self.assertEqual(response.status_code,302)
        response=browser.get(response.url)
        self.assertContains(response,'Your test invoice');self.assertContains(response,'TEST-INV-00001')
        for header,value in [('Cache-Control','no-store'),('Referrer-Policy','no-referrer'),('X-Robots-Tag','noindex')]:
            self.assertIn(value,response[header])
        other_bill=self.invoice(self.billing_plan)
        other=self.queue_copy(invoice_id=other_bill.pk,version=1)
        self.assertEqual(browser.get(reverse('workqueue:invoice_test_public',args=[other.pk])).status_code,403)
        self.assertEqual(browser.get(reverse('workqueue:invoice_test_public_pdf',args=[other.pk])).status_code,403)

    def test_session_cannot_bypass_expiry_or_revocation(self):
        delivery=self.queue_copy();browser=Browser()
        browser.post(reverse('workqueue:invoice_test_access'),{'token':self.raw(delivery)})
        revoke_delivery(delivery_id=delivery.pk,actor=self.actor)
        self.assertEqual(browser.get(reverse('workqueue:invoice_test_public',args=[delivery.pk])).status_code,403)
        self.assertEqual(browser.get(reverse('workqueue:invoice_test_public_pdf',args=[delivery.pk])).status_code,403)

    def test_invalid_token_never_reveals_invoice_or_client(self):
        response=Browser().post(reverse('workqueue:invoice_test_access'),{'token':'bad-code-that-is-at-least-20-characters'})
        self.assertContains(response,'unavailable or expired',status_code=400)
        self.assertNotContains(response,self.bill.number,status_code=400)
        self.assertNotContains(response,self.account.name,status_code=400)

    def test_public_payload_has_no_staff_notes_or_payment_reference(self):
        self.entry(self.bill,note='PRIVATE-LEDGER-NOTE',reference='PRIVATE-CHECK-REF')
        delivery=self.queue_copy();browser=Browser()
        browser.post(reverse('workqueue:invoice_test_access'),{'token':self.raw(delivery)})
        response=browser.get(reverse('workqueue:invoice_test_public',args=[delivery.pk]))
        self.assertNotContains(response,'PRIVATE-LEDGER-NOTE');self.assertNotContains(response,'PRIVATE-CHECK-REF')
        self.assertContains(response,'$75.00')
        pdf=browser.get(reverse('workqueue:invoice_test_public_pdf',args=[delivery.pk]))
        reader=PdfReader(BytesIO(pdf.content));text='\n'.join(page.extract_text() for page in reader.pages)
        self.assertIn('TEST-INV-00001',text);self.assertNotIn('PRIVATE-LEDGER-NOTE',text)
        self.assertNotIn('PRIVATE-CHECK-REF',text)

    def test_snapshot_stays_fixed_when_crm_or_current_invoice_changes(self):
        delivery=self.queue_copy();original=copy.deepcopy(delivery.snapshot)
        self.account.name='Changed company';self.account.save()
        self.bill.snapshot['notes']='Changed current notes';self.bill.save()
        delivery.refresh_from_db();self.assertEqual(delivery.snapshot,original)

    def test_link_opened_is_recorded_once_and_not_acceptance(self):
        delivery=self.queue_copy();raw=self.raw(delivery)
        self.assertIsNotNone(open_delivery(raw));self.assertIsNotNone(open_delivery(raw))
        self.assertEqual(self.bill.events.filter(action='Test link opened').count(),1)
        delivery.refresh_from_db();self.assertIsNotNone(delivery.opened_at)

    def test_review_then_send_requires_signed_exact_payload(self):
        url=reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk])
        data={'action':'review','token':str(uuid.uuid4()),'version':'1','contact':str(self.contact.pk),'subject':'Review this invoice','message':'Test message'}
        response=self.client.post(url,data)
        self.assertContains(response,'Review before sending');self.assertFalse(NativeInvoiceDelivery.objects.exists())
        review=response.context['review']
        response=self.client.post(url,{'action':'send','review':review})
        self.assertEqual(response.status_code,400);self.assertIsNotNone(response.context['preview'])
        response=self.client.post(url,{'action':'send','review':review,'confirm':'on','owner_email':'bad@example.invalid'})
        self.assertEqual(response.status_code,302)
        delivery=NativeInvoiceDelivery.objects.get()
        self.assertEqual(delivery.owner_recipient,'owner@example.invalid')
        self.assertContains(self.client.get(response.url),'Intended contact, not emailed')

    def test_tampered_expired_other_actor_or_invoice_review_is_rejected(self):
        url=reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk])
        response=self.client.post(url,{'action':'review','token':str(uuid.uuid4()),'version':'1','contact':str(self.contact.pk),'subject':'Test'})
        review=response.context['review'];payload=signing.loads(review,salt=SALT)
        for token in [review+'x',signing.dumps({**payload,'actor':'unrelated'},salt=SALT),signing.dumps({**payload,'invoice':str(uuid.uuid4())},salt=SALT),signing.dumps([],salt=SALT)]:
            self.assertEqual(self.client.post(url,{'action':'send','review':token,'confirm':'on'}).status_code,400)
        from unittest.mock import patch
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+1000):
            self.assertEqual(self.client.post(url,{'action':'send','review':review,'confirm':'on'}).status_code,400)
        self.assertFalse(NativeInvoiceDelivery.objects.exists())

    def test_csrf_and_staff_permissions_are_required(self):
        delivery=self.queue_copy();browser=Browser(enforce_csrf_checks=True);browser.force_login(self.actor)
        for url in [reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk]),reverse('workqueue:invoice_delivery_detail',args=[delivery.pk]),reverse('workqueue:invoice_test_access')]:
            self.assertEqual(browser.post(url,{}).status_code,403)
        viewer=get_user_model().objects.create_user('delivery-viewer',is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'));self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('workqueue:invoice_delivery_detail',args=[delivery.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('workqueue:invoice_delivery_detail',args=[delivery.pk]),{'action':'revoke','confirm':'yes'}).status_code,403)

    def test_csrf_access_page_allows_safari_same_origin_submission(self):
        response=Browser().get(reverse('workqueue:invoice_test_access'))
        self.assertEqual(response['Referrer-Policy'],'same-origin')
        self.assertContains(response,'action="'+reverse('workqueue:invoice_test_access')+'"')

    def test_email_preview_escapes_client_message_and_needs_no_frame_permissions(self):
        response=self.client.post(reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk]),
            {'action':'review','token':str(uuid.uuid4()),'version':'1','contact':str(self.contact.pk),'subject':'A quoted "subject"','message':'<img src=x onerror=bad()>'})
        self.assertContains(response,'class="invoice-email-preview"')
        self.assertNotContains(response,'<iframe')
        self.assertNotContains(response,'<img src=x onerror=bad()>')
        self.assertContains(response,'&lt;img src=x onerror=bad()&gt;')
        self.assertContains(response,'View private test invoice')



@skipUnless(connection.vendor=='postgresql','Production locking requires PostgreSQL.')
@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False,INTAKE_PUBLIC_BASE_URL='https://testserver',INTAKE_OWNER_EMAIL='owner@example.invalid')
class InvoiceDeliveryConcurrencyTests(DeliveryFixtures,TransactionTestCase):
    def setUp(self):self.seed_delivery()

    def test_simultaneous_confirmation_does_not_queue_two_emails(self):
        token=uuid.uuid4()
        def send(_):
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=self.actor.pk)
                return self.queue_copy(actor=actor,version=1,token=token).pk
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
        self.assertEqual(len(set(results)),1);self.assertEqual(NativeInvoiceDelivery.objects.count(),1)
