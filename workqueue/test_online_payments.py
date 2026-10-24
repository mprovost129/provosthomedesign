import copy
import hashlib
import hmac
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.management import call_command
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, Client as Browser, override_settings
from django.urls import reverse
from django.utils import timezone
from .test_native_invoices import InvoiceFixtures
from .native_invoice_models import NativeInvoiceEntry
from .native_invoices import balance
from .customer_statements import statement_for
from .services import EditConflict
from .test_payment_models import NativePaymentAttempt, NativeStripeEvent
from .test_payment_gateway import configuration, TestStripeGateway, GatewayUnavailable, safe_checkout_url
from .test_payments import reserve, create_or_resume, apply_session, refresh, start, handle_event, process_pending

SANDBOX = dict(NATIVE_STRIPE_TEST_ENABLED=True, NATIVE_STRIPE_TEST_SECRET_KEY='sk_test_FictionalKeyForUnitChecksOnly',
    NATIVE_STRIPE_TEST_WEBHOOK_SECRET='whsec_FictionalSigningSecretForChecks', INTAKE_PUBLIC_BASE_URL='https://testserver',
    WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)


def session_for(attempt, *, state='open'):
    metadata={'phd_test_attempt':str(attempt.pk),'purpose':'phd_native_invoice_sandbox'}
    session={'object':'checkout.session','id':'cs_test_'+attempt.pk.hex,'livemode':False,'mode':'payment','currency':'usd',
        'amount_total':int(attempt.amount*100),'metadata':metadata,'client_reference_id':str(attempt.pk),
        'payment_method_types':['card' if attempt.method=='card' else 'us_bank_account'],
        'payment_status':'paid' if state=='paid' else 'unpaid','status':'complete' if state in {'paid','pending','failed'} else state,
        'url':'https://checkout.stripe.com/c/pay/cs_test_'+attempt.pk.hex,'payment_intent':None}
    if state in {'paid','pending','failed'}:
        session['payment_intent']={'id':'pi_'+attempt.pk.hex,'livemode':False,'currency':'usd',
            'status':{'paid':'succeeded','pending':'processing','failed':'requires_payment_method'}[state],
            'amount_received':int(attempt.amount*100) if state=='paid' else 0,'metadata':metadata,
            'latest_charge':{'id':'ch_'+attempt.pk.hex,'amount_refunded':0,'disputed':False,
                'livemode':False,'paid':state=='paid','status':'succeeded' if state=='paid' else 'pending',
                'currency':'usd','amount':int(attempt.amount*100)}}
    return session


class FakeGateway:
    def __init__(self):self.sessions={};self.created=[];self.account='acct_TestBusiness'
    def account_id(self):return self.account
    def create(self,attempt):
        self.created.append(attempt.pk)
        session=session_for(attempt);self.sessions.setdefault(session['id'],session)
        return copy.deepcopy(self.sessions[session['id']])
    def retrieve(self,session_id):return copy.deepcopy(self.sessions[session_id])
    def expire(self,session_id):
        if self.sessions[session_id]['status']=='open':self.sessions[session_id]['status']='expired'
        return self.retrieve(session_id)


class PaymentFixtures(InvoiceFixtures):
    def seed_payments(self):
        self.seed_billing();self.bill=self.invoice(self.plan());self.gateway=FakeGateway()
    def reservation(self,**changes):
        self.bill.refresh_from_db()
        args=dict(invoice_id=self.bill.pk,version=self.bill.version,method='card',token=uuid.uuid4(),actor=self.actor,account_id=self.gateway.account)
        args.update(changes);return reserve(**args)
    def opened(self,**changes):return create_or_resume(self.reservation(**changes),gateway=self.gateway)
    def paid(self,attempt):
        self.gateway.sessions[attempt.session_id]=session_for(attempt,state='paid')
        return refresh(attempt,gateway=self.gateway)


@override_settings(**SANDBOX)
class OnlinePaymentTests(PaymentFixtures,TestCase):
    def setUp(self):self.seed_payments();self.client.force_login(self.actor)

    def test_configuration_fails_closed_and_never_uses_existing_commerce_keys(self):
        self.assertTrue(configuration()['ready'])
        for key in ['sk_live_ExampleLiveKey','rk_live_ExampleLiveKey','', 'sk_test_short']:
            with self.settings(NATIVE_STRIPE_TEST_SECRET_KEY=key,STRIPE_SECRET_KEY='sk_test_OtherKeyWouldBeWrong'):
                self.assertFalse(configuration()['ready'])
                with self.assertRaises(ValidationError):TestStripeGateway()
        with self.settings(NATIVE_STRIPE_TEST_ENABLED=False):
            with self.assertRaises(ValidationError):TestStripeGateway()

    def test_server_calculates_remaining_balance_and_reserves_invoice(self):
        self.entry(self.bill,amount='20')
        attempt=self.opened()
        self.assertEqual(attempt.amount,Decimal('80'))
        self.assertEqual(attempt.state,'open')
        with self.assertRaises(ValidationError):self.entry(self.bill)
        with self.assertRaises(ValidationError):self.void(self.bill)
        with self.assertRaises(ValidationError):self.reservation()

    def test_duplicate_submission_reuses_attempt_and_request_parameters(self):
        token=uuid.uuid4();version=self.bill.version
        original=self.reservation(token=token)
        same=self.reservation(token=token,version=version)
        self.assertEqual(original.pk,same.pk)
        with self.assertRaises(ValidationError):self.reservation(token=token,version=version,method='bank')
        self.assertEqual(NativePaymentAttempt.objects.count(),1)

    def test_stale_actions_and_invalid_amounts_are_rejected(self):
        with self.assertRaises(EditConflict):self.reservation(version=999)
        with self.assertRaises(ValidationError):self.reservation(method='paypal')
        self.entry(self.bill,amount='99.75')
        with self.assertRaises(ValidationError):self.reservation()

    def test_exact_card_settlement_is_recorded_once_and_statement_reconciles(self):
        attempt=self.opened();self.paid(attempt);self.paid(attempt)
        self.assertEqual(NativeInvoiceEntry.objects.filter(invoice=self.bill,kind='payment').count(),1)
        self.assertEqual(balance(self.bill)['due'],0)
        self.assertEqual(statement_for(self.account,actor=self.actor)['paid'],Decimal('100'))
        attempt.refresh_from_db()
        with self.assertRaises(ValidationError):self.reverse(self.bill,attempt.ledger_entry)

    def test_ach_pending_is_not_payment_and_failed_payment_releases_hold(self):
        attempt=self.opened(method='bank')
        self.gateway.sessions[attempt.session_id]=session_for(attempt,state='pending')
        result=refresh(attempt,gateway=self.gateway)
        self.assertEqual(result.state,'processing');self.assertEqual(balance(self.bill)['due'],Decimal('100'))
        with self.assertRaises(ValidationError):self.entry(self.bill)
        self.gateway.sessions[attempt.session_id]=session_for(attempt,state='failed')
        self.assertEqual(refresh(attempt,gateway=self.gateway).state,'failed')
        self.entry(self.bill);self.assertEqual(balance(self.bill)['due'],Decimal('75'))

    def test_later_ach_confirmation_settles_once(self):
        attempt=self.opened(method='bank')
        self.gateway.sessions[attempt.session_id]=session_for(attempt,state='pending')
        refresh(attempt,gateway=self.gateway)
        result=self.paid(attempt)
        self.assertEqual(result.state,'paid');self.assertEqual(result.ledger_entry.method,'bank')

    def test_expiration_is_provider_verified_and_processing_is_not_canceled(self):
        attempt=self.opened()
        self.assertEqual(refresh(attempt,gateway=self.gateway,expire=True).state,'expired')
        second=self.opened(method='bank')
        self.gateway.sessions[second.session_id]=session_for(second,state='pending')
        self.assertEqual(refresh(second,gateway=self.gateway,expire=True).state,'processing')
        self.assertEqual(balance(self.bill)['due'],Decimal('100'))

    def test_creation_timeout_keeps_reservation_and_retry_uses_same_attempt(self):
        attempt=self.reservation()
        with patch.object(self.gateway,'create',side_effect=GatewayUnavailable('uncertain')):
            with self.assertRaises(GatewayUnavailable):create_or_resume(attempt,gateway=self.gateway)
        self.assertEqual(NativePaymentAttempt.objects.get(pk=attempt.pk).state,'creating')
        with self.assertRaises(ValidationError):self.reservation()
        self.assertEqual(create_or_resume(attempt,gateway=self.gateway).pk,attempt.pk)

    def test_old_uncertain_creation_never_reuses_pruned_provider_idempotency_key(self):
        attempt=self.reservation();attempt.created_at=timezone.now()-timedelta(hours=24);attempt.save()
        with patch.object(self.gateway,'create') as create:
            with self.assertRaises(ValidationError):create_or_resume(attempt,gateway=self.gateway)
        create.assert_not_called()

    def test_recover_uncertain_attempt_verifies_exact_session(self):
        attempt=self.reservation();session=self.gateway.create(attempt)
        result=refresh(attempt,gateway=self.gateway,recovery_session=session['id'])
        self.assertEqual(result.session_id,session['id']);self.assertEqual(result.state,'open')

    def test_wrong_account_and_session_details_do_not_apply_payments(self):
        attempt=self.opened()
        wrongs=[('livemode',True),('currency','eur'),('amount_total',999),('mode','setup'),('client_reference_id',str(uuid.uuid4()))]
        for key,value in wrongs:
            session=session_for(attempt,state='paid');session[key]=value
            result=apply_session(attempt.pk,session,account_id=attempt.account_id)
            self.assertEqual(result.state,'review')
            self.assertFalse(result.ledger_entry_id)
        self.gateway.account='acct_OtherBusiness'
        with self.assertRaises(ValidationError):refresh(attempt,gateway=self.gateway)
        self.assertEqual(balance(self.bill)['due'],Decimal('100'))

    def test_paid_session_without_succeeded_exact_intent_is_not_settled(self):
        attempt=self.opened()
        for field,value in [('status','processing'),('amount_received',99),('livemode',True),('currency','eur'),('metadata',{})]:
            session=session_for(attempt,state='paid');session['payment_intent'][field]=value
            result=apply_session(attempt.pk,session,account_id=attempt.account_id)
            self.assertEqual(result.state,'review');self.assertFalse(result.ledger_entry_id)

    def test_bad_checkout_address_is_never_redirected(self):
        for url in ['http://checkout.stripe.com/c/pay/abc','https://checkout.stripe.com.evil.invalid/c/pay/abc',
                    'https://evil.invalid/','https://user@checkout.stripe.com/c/pay/abc','https://checkout.stripe.com:444/c/pay/abc']:
            self.assertFalse(safe_checkout_url(url))
        attempt=self.reservation();session=session_for(attempt);session['url']='https://evil.invalid/'
        self.assertEqual(apply_session(attempt.pk,session,account_id=attempt.account_id).state,'review')

    def test_missing_or_inconsistent_charge_never_settles_gross_intent(self):
        attempt=self.opened()
        for charge in ['ch_notExpanded',None,{'livemode':True,'paid':True}]:
            session=session_for(attempt,state='paid');session['payment_intent']['latest_charge']=charge
            result=apply_session(attempt.pk,session,account_id=attempt.account_id)
            self.assertEqual(result.state,'review');self.assertFalse(result.ledger_entry_id)

    def test_old_provider_reads_do_not_undo_paid_or_revive_expired_attempts(self):
        attempt=self.opened();self.paid(attempt)
        result=apply_session(attempt.pk,session_for(attempt),account_id=attempt.account_id)
        self.assertEqual(result.state,'paid');self.assertEqual(balance(self.bill)['due'],0)
        self.assertFalse(result.checkout_url)

    def test_balance_changed_outside_supported_workflow_is_held_for_review(self):
        attempt=self.opened()
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='payment',amount=Decimal('25'),method='cash',date=timezone.localdate(),note='Simulated outside correction')
        result=self.paid(attempt)
        self.assertEqual(result.state,'review');self.assertFalse(result.ledger_entry_id)
        self.assertEqual(balance(self.bill)['due'],Decimal('75'))

    def test_refund_or_dispute_is_visible_and_cannot_be_cleared_by_transient_error(self):
        attempt=self.opened();self.paid(attempt)
        self.gateway.sessions[attempt.session_id]['payment_intent']['latest_charge']['amount_refunded']=1000
        result=refresh(attempt,gateway=self.gateway)
        self.assertEqual((result.state,result.issue_code),('review','provider_adjustment'))
        with patch.object(self.gateway,'retrieve',side_effect=GatewayUnavailable('down')):
            with self.assertRaises(GatewayUnavailable):refresh(attempt,gateway=self.gateway)
        result.refresh_from_db();self.assertEqual(result.issue_code,'provider_adjustment')
        self.assertEqual(refresh(attempt,gateway=self.gateway).state,'review')

    def test_gateway_payload_uses_exact_server_amount_idempotency_and_no_crm_data(self):
        attempt=self.reservation(method='bank')
        with patch('stripe.StripeClient') as client:
            gateway=TestStripeGateway();gateway.create(attempt)
        call=client.return_value.v1.checkout.sessions.create.call_args
        params=call.args[0]
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'],10000)
        self.assertEqual(params['payment_method_types'],['us_bank_account'])
        self.assertEqual(call.kwargs['options']['idempotency_key'],'phd-sandbox-'+str(attempt.pk))
        self.assertNotIn(self.account.name,str(params));self.assertNotIn(self.item.contact_email,str(params))
        self.assertNotIn('customer_email',params);self.assertNotIn('setup_future_usage',params['payment_intent_data'])

    def test_verified_webhook_retrieves_latest_state_and_deduplicates(self):
        attempt=self.opened();self.gateway.sessions[attempt.session_id]=session_for(attempt,state='paid')
        event={'id':'evt_TestOne','type':'checkout.session.completed','livemode':False,'data':{'object':{'id':attempt.session_id}}}
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):
            handle_event(event);handle_event(event)
        self.assertEqual(NativeStripeEvent.objects.count(),1)
        self.assertEqual(NativeInvoiceEntry.objects.filter(invoice=self.bill).count(),1)

    def test_live_or_connect_events_are_rejected(self):
        event={'id':'evt_TestOne','type':'checkout.session.completed','livemode':True,'data':{'object':{}}}
        with self.assertRaises(ValidationError):handle_event(event)
        event.update(livemode=False,account='acct_Connected')
        with self.assertRaises(ValidationError):handle_event(event)

    def test_unrelated_checkout_events_without_our_metadata_are_ignored(self):
        for metadata in [None,{},[], 'unrelated']:
            handle_event({'id':'evt_Unrelated','type':'checkout.session.completed','livemode':False,
                'data':{'object':{'id':'cs_test_OtherIntegration','metadata':metadata}}})
        self.assertEqual(NativeStripeEvent.objects.count(),0)

    def test_webhook_raw_signature_required_even_though_csrf_exempt(self):
        browser=Browser(enforce_csrf_checks=True)
        event={'id':'evt_SignedCheck','type':'ignored.test','livemode':False,'data':{'object':{}}}
        payload=json.dumps(event).encode();stamp=str(int(time.time()))
        digest=hmac.new(SANDBOX['NATIVE_STRIPE_TEST_WEBHOOK_SECRET'].encode(),stamp.encode()+b'.'+payload,hashlib.sha256).hexdigest()
        url=reverse('workqueue:test_payment_webhook')
        self.assertEqual(browser.post(url,data=payload,content_type='application/json').status_code,400)
        self.assertEqual(browser.post(url,data=payload,content_type='application/json',HTTP_STRIPE_SIGNATURE=f't={stamp},v1={digest}').status_code,200)
        self.assertEqual(browser.post(url,data=b'x'*262145,content_type='application/json').status_code,413)
        with self.settings(NATIVE_STRIPE_TEST_ENABLED=False):self.assertEqual(browser.post(url,data=payload,content_type='application/json').status_code,503)

    def test_worker_recovers_missed_webhook_and_does_not_create_new_checkouts(self):
        attempt=self.opened();self.gateway.sessions[attempt.session_id]=session_for(attempt,state='paid')
        NativePaymentAttempt.objects.filter(pk=attempt.pk).update(last_checked_at=timezone.now()-timedelta(minutes=10))
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):
            self.assertEqual(process_pending(),(1,0))
        self.assertEqual(len(self.gateway.created),1)
        self.assertEqual(balance(self.bill)['due'],0)

    def test_view_permissions_csrf_and_no_success_from_return_url(self):
        attempt=self.opened()
        url=reverse('workqueue:test_payment_detail',args=[attempt.pk])
        response=self.client.get(url,{'returned':'1','paid':'true'})
        self.assertContains(response,'Ready for test payment')
        self.assertEqual(balance(self.bill)['due'],Decimal('100'))
        self.assertIn('no-store',response['Cache-Control']);self.assertEqual(response['Referrer-Policy'],'same-origin')
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.actor)
        self.assertEqual(browser.post(url,{'action':'refresh'}).status_code,403)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)
        staff=get_user_model().objects.create_user('payment-viewer',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'action':'refresh'}).status_code,403)
        with self.assertRaises(PermissionDenied):self.reservation(actor=staff)

    def test_checkout_ui_requires_explicit_confirmation_and_cannot_override_amount(self):
        url=reverse('workqueue:test_payment_start',args=[self.bill.pk])
        data={'token':uuid.uuid4(),'version':self.bill.version,'method':'card','amount':'1'}
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertEqual(NativePaymentAttempt.objects.count(),0)
        data['confirm']='on'
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        attempt=NativePaymentAttempt.objects.get()
        self.assertContains(response,'href="'+attempt.checkout_url+'"')
        self.assertIn("form-action 'self'",response['Content-Security-Policy'])
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):
            continued=self.client.post(reverse('workqueue:test_payment_detail',args=[attempt.pk]),{'action':'continue'})
        self.assertContains(continued,'Continue to Stripe test checkout')
        self.assertEqual(NativePaymentAttempt.objects.count(),1)
        self.assertEqual(NativePaymentAttempt.objects.get().amount,Decimal('100'))

    def test_disabled_ui_is_reviewable_without_exposing_secrets(self):
        with self.settings(NATIVE_STRIPE_TEST_ENABLED=False):response=self.client.get(reverse('workqueue:test_payments'))
        self.assertContains(response,'Not connected yet')
        self.assertNotContains(response,SANDBOX['NATIVE_STRIPE_TEST_SECRET_KEY'])
        self.assertNotContains(response,SANDBOX['NATIVE_STRIPE_TEST_WEBHOOK_SECRET'])

    def test_cron_task_only_dispatches_when_explicitly_enabled(self):
        with patch('workqueue.management.commands.run_queue_jobs.call_command') as dispatch, patch('workqueue.management.commands.run_queue_jobs.close_old_connections'):
            call_command('run_queue_jobs',stdout=StringIO())
        self.assertIn('reconcile_test_payments',[call.args[0] for call in dispatch.call_args_list])


@override_settings(**SANDBOX)
class PaymentConcurrencyTests(PaymentFixtures,TransactionTestCase):
    def setUp(self):self.seed_payments()

    @skipUnless(connection.vendor=='postgresql','Real PostgreSQL locking check')
    def test_concurrent_confirmations_create_exactly_one_payment(self):
        attempt=self.opened();session=session_for(attempt,state='paid')
        def settle(_):
            close_old_connections()
            try:return apply_session(attempt.pk,copy.deepcopy(session),account_id=attempt.account_id).state
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(settle,range(2)))
        self.assertEqual(results,['paid','paid'])
        self.assertEqual(NativeInvoiceEntry.objects.filter(invoice=self.bill,kind='payment').count(),1)
