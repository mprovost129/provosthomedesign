import copy,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,close_old_connections
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .test_invoice_delivery import DeliveryFixtures
from .test_online_payments import FakeGateway,session_for,SANDBOX
from .test_refund_accounting import RefundGateway
from .test_payment_models import NativePaymentAttempt
from .client_payment_models import NativeTestPaymentReceipt
from .invoice_delivery import queue_invoice_test,revoke_delivery,open_delivery
from .client_payments import start_for_delivery,change_attempt,ensure_receipt,receipt_projection
from .payment_receipt_pdf import receipt_pdf
from .test_payments import refresh,reserve
from .native_invoices import balance
from .models import NotificationDelivery
from .services import EditConflict

SETTINGS={**SANDBOX,'NATIVE_STRIPE_TEST_CLIENT_CHECKOUT_ENABLED':True,'WORK_QUEUE_ENABLED':True,
 'WORK_INTAKE_ENABLED':True,'BOOKING_ENABLED':False,'INTAKE_PUBLIC_BASE_URL':'https://testserver',
 'INTAKE_OWNER_EMAIL':'owner@example.invalid','RATELIMIT_ENABLE':False,
 'EMAIL_BACKEND':'django.core.mail.backends.locmem.EmailBackend'}


class ClientPaymentFixtures(DeliveryFixtures):
    def seed_client_payment(self):
        self.seed_delivery();self.gateway=RefundGateway()
        self.delivery=self.queue_copy(allow_test_checkout=True)
        self.public=Browser();self.open_copy(self.public,self.delivery)

    def open_copy(self,browser,delivery):
        return browser.post(reverse('workqueue:invoice_test_access'),{'token':self.raw(delivery)})

    def checkout(self,**changes):
        self.bill.refresh_from_db()
        args=dict(delivery_id=self.delivery.pk,version=self.bill.version,method='card',token=uuid.uuid4(),gateway=self.gateway)
        args.update(changes);return start_for_delivery(**args)

    def paid(self,attempt):
        self.gateway.sessions[attempt.session_id]=session_for(attempt,state='paid')
        return refresh(attempt,gateway=self.gateway)

    def url(self,name,attempt=None,delivery=None):
        return reverse('workqueue:'+name,args=[(delivery or self.delivery).pk,*([attempt.pk] if attempt else [])])


@override_settings(**SETTINGS)
class ClientPaymentTests(ClientPaymentFixtures,TestCase):
    def setUp(self):self.seed_client_payment()

    def test_flag_and_explicit_delivery_opt_in_required(self):
        with self.settings(NATIVE_STRIPE_TEST_CLIENT_CHECKOUT_ENABLED=False):
            with self.assertRaises(ValidationError):self.checkout()
        self.delivery.allow_test_checkout=False;self.delivery.save()
        with self.assertRaises(PermissionDenied):self.checkout()
        self.assertEqual(self.public.get(self.url('invoice_test_checkout')).status_code,403)
        self.assertFalse(NativePaymentAttempt.objects.exists());self.assertFalse(self.gateway.created)

    def test_only_owner_can_opt_in_and_default_copy_stays_read_only(self):
        self.actor.is_superuser=False;self.actor.save()
        self.actor.user_permissions.add(*Permission.objects.filter(codename__in=['view_workitem','change_workitem']))
        with self.assertRaises(ValidationError):self.queue_copy(allow_test_checkout=True)
        self.delivery.allow_test_checkout=False;self.delivery.save()
        response=self.public.get(self.url('invoice_test_public'))
        self.assertNotContains(response,'Pay test invoice')

    def test_anonymous_bearer_session_can_create_only_its_invoice_checkout(self):
        attempt=self.checkout()
        self.assertIsNone(attempt.actor_id);self.assertEqual(attempt.delivery_id,self.delivery.pk)
        self.assertEqual(attempt.amount,self.bill.amount)
        self.assertFalse(get_user_model().objects.exclude(pk=self.actor.pk).filter(is_staff=True).exists())

    def test_missing_or_wrong_session_cannot_start_inspect_poll_or_download(self):
        attempt=self.paid(self.checkout());receipt=attempt.test_receipt
        stranger=Browser()
        for name in ['invoice_test_checkout','invoice_test_payment','invoice_test_payment_state']:
            url=self.url(name,attempt if name!='invoice_test_checkout' else None)
            self.assertEqual(stranger.get(url).status_code,403)
            self.assertIn('no-store',stranger.get(url)['Cache-Control'])
        self.assertEqual(stranger.get(reverse('workqueue:invoice_test_receipt',args=[self.delivery.pk,receipt.pk])).status_code,403)
        self.assertEqual(stranger.post(self.url('invoice_test_checkout'),{'method':'card'}).status_code,403)

    def test_cross_invoice_attempts_and_receipts_are_not_accessible(self):
        attempt=self.paid(self.checkout());receipt=attempt.test_receipt
        other=self.invoice(self.billing_plan)
        other_copy=self.queue_copy(invoice_id=other.pk,version=other.version,allow_test_checkout=True)
        browser=Browser();self.open_copy(browser,other_copy)
        self.assertEqual(browser.get(self.url('invoice_test_payment',attempt,other_copy)).status_code,404)
        self.assertEqual(browser.get(reverse('workqueue:invoice_test_receipt',args=[other_copy.pk,receipt.pk])).status_code,404)

    def test_existing_staff_attempt_is_not_adopted_by_private_link(self):
        self.bill.refresh_from_db()
        staff=reserve(invoice_id=self.bill.pk,version=self.bill.version,method='card',token=uuid.uuid4(),actor=self.actor,account_id=self.gateway.account)
        with self.assertRaises(ValidationError):self.checkout()
        self.assertEqual(self.public.get(self.url('invoice_test_payment',staff)).status_code,404)

    def test_nonce_is_idempotent_and_origin_cannot_be_substituted(self):
        self.bill.refresh_from_db();version=self.bill.version;token=uuid.uuid4()
        a=self.checkout(version=version,token=token);b=self.checkout(version=version,token=token)
        self.assertEqual(a.pk,b.pk);self.assertEqual(len(self.gateway.created),1)
        with self.assertRaises(ValidationError):self.checkout(version=version,token=token,method='bank')
        with self.assertRaises(ValidationError):
            reserve(invoice_id=self.bill.pk,version=version,method='card',token=token,actor=self.actor,account_id=self.gateway.account)

    def test_server_amount_and_stale_version_guard(self):
        self.bill.refresh_from_db();version=self.bill.version
        self.bill.version+=1;self.bill.save()
        with self.assertRaises(EditConflict):self.checkout(version=version)
        self.assertFalse(self.gateway.created)
        a=self.checkout();self.assertEqual(a.amount,self.bill.amount)

    def test_revocation_expiry_void_and_owner_change_block_new_checkout(self):
        with self.settings(INTAKE_OWNER_EMAIL='changed@example.invalid'):
            with self.assertRaises(ValidationError):self.checkout()
        for field,value in [('expires_at',timezone.now()-timedelta(seconds=1)),('revoked_at',timezone.now())]:
            old=getattr(self.delivery,field);setattr(self.delivery,field,value);self.delivery.save()
            with self.assertRaises(PermissionDenied):self.checkout()
            setattr(self.delivery,field,old);self.delivery.save()
        self.bill.voided_at=timezone.now();self.bill.save()
        with self.assertRaises(PermissionDenied):self.checkout()

    def test_database_attempt_limit_prevents_cookie_reset_bypass(self):
        for _ in range(5):
            attempt=self.checkout()
            change_attempt(delivery_id=self.delivery.pk,attempt_id=attempt.pk,action='expire',gateway=self.gateway)
        with self.assertRaises(ValidationError):self.checkout()
        self.assertEqual(NativePaymentAttempt.objects.count(),5)

    def test_pending_bank_payment_has_no_receipt_and_cannot_be_canceled(self):
        a=self.checkout(method='bank');self.gateway.sessions[a.session_id]=session_for(a,state='pending')
        a=refresh(a,gateway=self.gateway)
        self.assertEqual(a.state,'processing');self.assertFalse(NativeTestPaymentReceipt.objects.exists())
        with self.assertRaises(ValidationError):change_attempt(delivery_id=self.delivery.pk,attempt_id=a.pk,action='expire',gateway=self.gateway)
        self.assertContains(self.public.get(self.url('invoice_test_payment',a)),'Your test bank payment is pending')
        self.assertEqual(balance(self.bill)['due'],100)

    def test_verified_card_and_ach_receipts_are_created_exactly_once(self):
        a=self.paid(self.checkout());self.paid(a);ensure_receipt(a)
        self.assertEqual(NativeTestPaymentReceipt.objects.count(),1)
        self.assertEqual(a.test_receipt.number,'TEST-RC-00001')
        self.assertEqual(a.test_receipt.snapshot['amount'],'100.00')
        self.assertEqual(a.test_receipt.snapshot['client_address'],self.bill.snapshot.get('billing_address',''))
        self.assertEqual(self.bill.entries.filter(kind='payment').count(),1)
        other=self.invoice(self.billing_plan)
        copy=self.queue_copy(invoice_id=other.pk,version=other.version,allow_test_checkout=True)
        b=self.paid(self.checkout(delivery_id=copy.pk,version=copy.invoice.version,method='bank'))
        self.assertEqual(b.test_receipt.number,'TEST-RC-00002')

    def test_late_success_after_link_revocation_records_finances_but_denies_access(self):
        a=self.checkout();revoke_delivery(delivery_id=self.delivery.pk,actor=self.actor)
        a=self.paid(a)
        self.assertEqual(NativeTestPaymentReceipt.objects.count(),1)
        self.assertEqual(balance(self.bill)['due'],0)
        self.assertEqual(self.public.get(self.url('invoice_test_payment',a)).status_code,403)

    def test_private_receipt_is_immutable_after_refund_and_contains_no_internal_data(self):
        a=self.paid(self.checkout());receipt=a.test_receipt;original=copy.deepcopy(receipt.snapshot)
        charge=self.gateway.sessions[a.session_id]['payment_intent']['latest_charge']
        self.gateway.refunds=[{'object':'refund','id':'re_External','currency':'usd','amount':10000,'status':'succeeded',
            'charge':charge['id'],'payment_intent':a.payment_intent_id,'metadata':{}}]
        refresh(a,gateway=self.gateway);receipt.refresh_from_db()
        self.assertEqual(receipt.snapshot,original)
        pdf=PdfReader(BytesIO(receipt_pdf(receipt_projection(receipt))))
        text=''.join(page.extract_text() for page in pdf.pages)
        for safe in ['TEST-RC-00001','100.00','NO REAL MONEY RECEIVED','Later refunds']:self.assertIn(safe,text)
        for private in [str(a.pk),a.payment_intent_id,a.session_id,'Stripe sandbox confirmed this TEST payment']:
            self.assertNotIn(private,text)
        response=self.public.get(reverse('workqueue:invoice_test_receipt',args=[self.delivery.pk,receipt.pk]))
        self.assertEqual(response['Content-Type'],'application/pdf');self.assertIn('no-store',response['Cache-Control'])

    def test_status_projection_has_no_provider_or_staff_details(self):
        a=self.checkout()
        response=self.public.get(self.url('invoice_test_payment',a))
        self.assertContains(response,'Continue same test checkout')
        for private in [a.session_id,a.account_id,'Test audit details','Prepare test refund']:self.assertNotContains(response,private)
        data=self.public.get(self.url('invoice_test_payment_state',a)).json()
        self.assertEqual(set(data),{'fingerprint'});self.assertEqual(len(data['fingerprint']),64)

    def test_poll_and_return_get_do_not_contact_provider_or_create_receipt(self):
        a=self.checkout()
        with patch('workqueue.test_payments.TestStripeGateway') as provider:
            self.public.get(self.url('invoice_test_payment',a)+'?returned=1')
            self.public.get(self.url('invoice_test_payment_state',a))
        provider.assert_not_called();self.assertFalse(NativeTestPaymentReceipt.objects.exists())

    def test_csrf_protection_and_same_origin_form_policy(self):
        browser=Browser(enforce_csrf_checks=True)
        session=browser.session;session['invoice_test_delivery']=str(self.delivery.pk);session.save()
        url=self.url('invoice_test_checkout')
        self.assertEqual(browser.post(url,{'method':'card'}).status_code,403)
        self.assertEqual(browser.get(url)['Referrer-Policy'],'same-origin')

    def test_signed_email_review_freezes_checkout_opt_in(self):
        self.client.force_login(self.actor);self.bill.refresh_from_db()
        url=reverse('workqueue:invoice_delivery_compose',args=[self.bill.pk])
        response=self.client.post(url,{'action':'review','token':uuid.uuid4(),'version':self.bill.version,
            'contact':self.contact.pk,'subject':'Fictional copy','message':'Test','allow_test_checkout':'on'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['preview']['allow_test_checkout'])
        self.assertContains(response,'Enabled on this owner-only private copy')

    def test_no_new_email_is_generated_on_payment_or_receipt(self):
        count=NotificationDelivery.objects.count();a=self.paid(self.checkout())
        ensure_receipt(a);self.assertEqual(NotificationDelivery.objects.count(),count)

    def test_gateway_returns_to_only_fixed_private_status_route(self):
        a=self.checkout()
        from .test_payment_gateway import TestStripeGateway
        with patch('stripe.StripeClient') as client:
            TestStripeGateway().create(a)
        params=client.return_value.v1.checkout.sessions.create.call_args.args[0]
        self.assertEqual(params['success_url'],'https://testserver'+self.url('invoice_test_payment',a)+'?returned=1')
        self.assertEqual(params['cancel_url'],params['success_url'])
        for unsafe in ['customer_email','receipt_email','invoice_creation']:self.assertNotIn(unsafe,params)


@override_settings(**SETTINGS)
class ClientPaymentConcurrencyTests(ClientPaymentFixtures,TransactionTestCase):
    def setUp(self):self.seed_client_payment()

    @skipUnless(connection.vendor=='postgresql','PostgreSQL locking check')
    def test_competing_private_checkouts_reserve_once(self):
        self.bill.refresh_from_db();version=self.bill.version
        def run(_):
            close_old_connections()
            try:
                self.checkout(version=version);return 'reserved'
            except (ValidationError,EditConflict):return 'blocked'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertCountEqual(results,['reserved','blocked']);self.assertEqual(NativePaymentAttempt.objects.count(),1)

    @skipUnless(connection.vendor=='postgresql','PostgreSQL locking check')
    def test_concurrent_settlement_issues_one_receipt_and_one_payment(self):
        a=self.checkout();self.gateway.sessions[a.session_id]=session_for(a,state='paid')
        def run(_):
            close_old_connections()
            try:refresh(NativePaymentAttempt.objects.get(pk=a.pk),gateway=self.gateway)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(2)))
        self.assertEqual(NativeTestPaymentReceipt.objects.count(),1)
        self.assertEqual(self.bill.entries.filter(kind='payment').count(),1)
