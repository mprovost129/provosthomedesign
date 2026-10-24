import copy,uuid
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connection,close_old_connections
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from .test_online_payments import PaymentFixtures,FakeGateway,SANDBOX,session_for
from .test_refund_models import NativeRefundRequest,NativePaymentAdjustment
from .test_payment_models import NativePaymentAttempt,NativeStripeEvent
from .native_invoice_models import NativeInvoiceEntry
from .native_invoices import balance
from .customer_statements import statement_for
from .test_payment_gateway import GatewayUnavailable,TestStripeGateway,configuration
from .test_payments import refresh,handle_event
from .test_refunds import reserve_refund,submit_refund,begin_read,reconcile


class RefundGateway(FakeGateway):
    def __init__(self):
        super().__init__();self.refunds=[];self.disputes=[];self.requests=[]
    def adjustments(self,*args,**kwargs):return copy.deepcopy(self.refunds),copy.deepcopy(self.disputes)
    def create_refund(self,request):
        self.requests.append(request.pk)
        existing=next((obj for obj in self.refunds if obj['metadata'].get('phd_test_refund')==str(request.pk)),None)
        if existing:return copy.deepcopy(existing)
        session=self.sessions[request.attempt.session_id]
        obj={'id':'re_'+request.pk.hex,'object':'refund','amount':int(request.amount*100),'currency':'usd',
            'charge':session['payment_intent']['latest_charge']['id'],'payment_intent':request.attempt.payment_intent_id,
            'status':'succeeded','metadata':{'phd_test_refund':str(request.pk),'purpose':'phd_native_invoice_sandbox'}}
        self.refunds.append(obj);return copy.deepcopy(obj)


class RefundFixtures(PaymentFixtures):
    def seed_refunds(self):
        self.seed_payments();self.gateway=RefundGateway();self.attempt=self.opened();self.paid(self.attempt);self.attempt.refresh_from_db()
        self.client.force_login(self.actor)
    def request(self,**changes):
        self.bill.refresh_from_db()
        args=dict(attempt_id=self.attempt.pk,version=self.bill.version,amount='30',credit_work=False,note='Fictional refund reason',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return reserve_refund(**args)
    def refund(self,amount=3000,status='succeeded',pid='re_External'):
        return {'id':pid,'object':'refund','amount':amount,'currency':'usd','status':status,'metadata':{},
            'charge':self.gateway.sessions[self.attempt.session_id]['payment_intent']['latest_charge']['id'],
            'payment_intent':self.attempt.payment_intent_id}
    def dispute(self,status='needs_response',amounts=(-4000,)):
        obj=self.refund(4000,status,'du_Case');obj.update(object='dispute',livemode=False,
            balance_transactions=[{'id':'txn_'+str(i),'object':'balance_transaction','currency':'usd','amount':amount,'fee':1500,'net':amount-1500} for i,amount in enumerate(amounts)])
        return obj
    def sync(self):return refresh(self.attempt,gateway=self.gateway)


@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class RefundAccountingTests(RefundFixtures,TestCase):
    def setUp(self):self.seed_refunds()

    def test_partial_refund_reopens_debt_and_preserves_original_payment(self):
        self.gateway.refunds=[self.refund()];self.sync();self.sync()
        self.assertEqual(balance(self.bill)['paid'],70);self.assertEqual(balance(self.bill)['due'],30)
        self.assertEqual(self.bill.entries.filter(kind='payment').count(),1)
        self.assertEqual(self.bill.entries.filter(kind='refund').count(),1)
        self.assertEqual(statement_for(self.account,actor=self.actor)['due'],30)

    def test_full_refund_then_failed_refund_recovers_payment(self):
        self.gateway.refunds=[self.refund(10000)];self.sync()
        self.assertEqual(balance(self.bill)['due'],100)
        self.gateway.refunds[0]['status']='failed';self.sync();self.sync()
        self.assertEqual(balance(self.bill)['due'],0)
        self.assertEqual(self.bill.entries.filter(kind='recovery').count(),1)

    def test_pending_refund_holds_edits_and_is_not_a_return(self):
        self.gateway.refunds=[self.refund(status='pending')]
        result=self.sync();self.assertEqual(result.issue_code,'adjustment_pending')
        self.assertEqual(balance(self.bill)['paid'],100)
        with self.assertRaises(ValidationError):self.entry(self.bill)
        with self.assertRaises(ValidationError):self.request()
        self.gateway.refunds[0]['status']='failed';self.sync()
        self.assertEqual(self.attempt.adjustments.first().applied_amount,0)

    def test_confirmed_refund_can_issue_linked_work_credit_and_failure_reverses_it(self):
        request=self.request(credit_work=True)
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):
            submit_refund(request,actor=self.actor,gateway=self.gateway)
        request.refresh_from_db();self.assertEqual(request.state,'succeeded')
        self.assertEqual(balance(self.bill)['due'],0);self.assertEqual(balance(self.bill)['credited'],30)
        credit=self.bill.entries.get(kind='credit');self.assertTrue(credit.credit_number.startswith('TEST-CR-'))
        with self.assertRaises(ValidationError):self.reverse(self.bill,credit)
        self.gateway.refunds[0]['status']='failed';self.sync()
        self.assertEqual(balance(self.bill)['credited'],0);self.assertEqual(balance(self.bill)['due'],0)
        self.assertTrue(self.bill.entries.filter(kind='reversal',reverses=credit).exists())

    def test_pending_then_successful_linked_credit_posts_only_at_success(self):
        request=self.request(credit_work=True)
        obj=self.gateway.create_refund(request);self.gateway.refunds[0]['status']='pending';self.sync()
        self.assertEqual(balance(self.bill)['credited'],0)
        self.gateway.refunds[0]['status']='succeeded';self.sync()
        self.assertEqual(balance(self.bill)['credited'],30)

    def test_refund_timeout_retry_has_one_provider_reference(self):
        request=self.request()
        self.gateway.create_refund(request)
        with patch.object(self.gateway,'create_refund',side_effect=GatewayUnavailable('uncertain')):
            with self.assertRaises(GatewayUnavailable):submit_refund(request,actor=self.actor,gateway=self.gateway)
        with self.assertRaises(ValidationError):self.request()
        self.sync();request.refresh_from_db();self.assertEqual(request.state,'succeeded')
        submit_refund(request,actor=self.actor,gateway=self.gateway)
        self.assertEqual(len(self.gateway.refunds),1);self.assertEqual(self.bill.entries.filter(kind='refund').count(),1)

    def test_old_uncertain_refund_never_reuses_pruned_idempotency_key(self):
        request=self.request();request.created_at=timezone.now()-timedelta(hours=24);request.save()
        with self.assertRaises(ValidationError):submit_refund(request,actor=self.actor,gateway=self.gateway)
        self.assertEqual(self.gateway.requests,[])

    def test_refund_request_nonce_and_remaining_amount_are_enforced(self):
        self.bill.refresh_from_db();token=uuid.uuid4();version=self.bill.version;request=self.request(token=token)
        self.assertEqual(self.request(token=token,version=version).pk,request.pk)
        with self.assertRaises(ValidationError):self.request(token=token,version=version,amount='31')
        self.gateway.create_refund(request);self.sync()
        with self.assertRaises(ValidationError):self.request(amount='71')

    def test_dispute_withdrawal_and_won_recovery_exclude_fees(self):
        self.gateway.disputes=[self.dispute()];self.sync()
        self.assertEqual(balance(self.bill)['due'],40);self.assertEqual(self.bill.entries.get(kind='return').amount,40)
        with self.assertRaises(ValidationError):self.request()
        self.gateway.disputes=[self.dispute('won',(-4000,4000))];self.sync();self.sync()
        self.assertEqual(balance(self.bill)['due'],0);self.assertEqual(self.bill.entries.get(kind='recovery').amount,40)
        self.attempt.refresh_from_db();self.assertEqual(self.attempt.state,'paid')

    def test_lost_bank_return_reopens_invoice_without_canceling_work(self):
        self.gateway.disputes=[self.dispute('lost')];self.sync()
        self.assertEqual(balance(self.bill)['due'],40);self.assertEqual(balance(self.bill)['credited'],0)
        self.assertEqual(statement_for(self.account,actor=self.actor)['due'],40)

    def test_inquiry_without_withdrawal_has_no_accounting_debit(self):
        self.gateway.disputes=[self.dispute('warning_needs_response',())];self.sync()
        self.assertEqual(balance(self.bill)['due'],0);self.assertFalse(self.bill.entries.filter(kind='return').exists())

    def test_stale_provider_snapshot_cannot_undo_newer_refund(self):
        older=begin_read(self.attempt.pk);newer=begin_read(self.attempt.pk)
        session=self.gateway.sessions[self.attempt.session_id]
        reconcile(self.attempt.pk,session,[self.refund()],[],account_id=self.gateway.account,generation=newer)
        reconcile(self.attempt.pk,session,[],[],account_id=self.gateway.account,generation=older)
        self.assertEqual(balance(self.bill)['due'],30)

    def test_incomplete_or_mismatched_histories_preserve_ledger_and_hold_review(self):
        self.gateway.refunds=[self.refund()];self.sync()
        for rows in [[],[dict(self.refund(),currency='eur')],[dict(self.refund(),charge='ch_Wrong')],
                     [dict(self.refund(),amount=20000)],[dict(self.refund(),status='unknown')]]:
            self.gateway.refunds=rows
            with self.assertRaises(ValidationError):self.sync()
            self.assertEqual(balance(self.bill)['due'],30)
        self.attempt.refresh_from_db();self.assertEqual(self.attempt.issue_code,'adjustment_unconfirmed')

    def test_overlapping_refund_and_dispute_are_not_silently_double_debited(self):
        self.gateway.refunds=[self.refund(8000)];self.gateway.disputes=[self.dispute()]
        with self.assertRaises(ValidationError):self.sync()
        self.assertEqual(balance(self.bill)['due'],0)

    def test_refund_failure_after_repayment_shows_credit_balance_and_statement_reconciles(self):
        self.gateway.refunds=[self.refund()];self.sync();self.entry(self.bill,amount='30')
        self.gateway.refunds[0]['status']='failed';self.sync()
        self.assertEqual(balance(self.bill)['due'],-30);self.assertEqual(balance(self.bill)['status'],'Credit balance')
        self.assertEqual(statement_for(self.account,actor=self.actor)['due'],-30)

    def test_recovery_on_previously_voided_refunded_invoice_is_account_credit(self):
        self.gateway.refunds=[self.refund(10000)];self.sync();self.void(self.bill)
        self.gateway.refunds[0]['status']='failed';self.sync();self.bill.refresh_from_db()
        self.assertEqual(balance(self.bill)['due'],-100)
        self.assertEqual(statement_for(self.account,actor=self.actor)['due'],-100)

    def test_signed_event_reads_current_provider_history_and_deduplicates(self):
        self.gateway.refunds=[self.refund()]
        event={'id':'evt_Refund','type':'refund.updated','livemode':False,'data':{'object':self.refund(status='pending')}}
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway):handle_event(event);handle_event(event)
        self.assertEqual(balance(self.bill)['due'],30);self.assertEqual(NativeStripeEvent.objects.count(),1)

    def test_only_owner_can_issue_test_refunds_and_disabled_flag_fails_closed(self):
        staff=get_user_model().objects.create_user('billing-staff',is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_workitem','change_workitem']))
        with self.assertRaises(PermissionDenied):self.request(actor=staff)
        with self.settings(NATIVE_STRIPE_TEST_REFUNDS_ENABLED=False):
            with self.assertRaises(ValidationError):self.request()
        self.client.force_login(staff)
        self.assertEqual(self.client.post(reverse('workqueue:test_refund_prepare',args=[self.attempt.pk]),{}).status_code,403)

    def test_review_must_be_confirmed_signed_and_recent_before_provider_write(self):
        url=reverse('workqueue:test_refund_prepare',args=[self.attempt.pk]);self.bill.refresh_from_db()
        response=self.client.post(url,{'action':'review','token':uuid.uuid4(),'version':self.bill.version,'amount':'20','treatment':'credit','note':'Fictional cancellation'})
        self.assertContains(response,'Confirm the exact test refund');self.assertFalse(NativeRefundRequest.objects.exists())
        signed=response.context['review']['signed']
        self.assertEqual(self.client.post(url,{'action':'confirm','review':signed}).status_code,400)
        self.assertEqual(self.client.post(url,{'action':'confirm','review':signed+'x','confirm':'yes'}).status_code,400)
        with patch('workqueue.test_payments.TestStripeGateway',return_value=self.gateway),patch('workqueue.test_refunds.TestStripeGateway',return_value=self.gateway):
            result=self.client.post(url,{'action':'confirm','review':signed,'confirm':'yes'})
        self.assertEqual(result.status_code,302);self.assertEqual(balance(self.bill)['credited'],20)
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.actor)
        self.assertEqual(browser.post(url,{}).status_code,403)

    def test_views_show_adjustments_without_internal_notes_in_statement(self):
        self.gateway.refunds=[self.refund()];self.sync()
        response=self.client.get(reverse('workqueue:test_payment_detail',args=[self.attempt.pk]))
        self.assertContains(response,'Refunds, bank returns');self.assertContains(response,'re_External')
        self.assertIn('no-store',response['Cache-Control'])
        statement=statement_for(self.account,actor=self.actor)
        self.assertIn('Refund',[row['kind'] for row in statement['activity']])
        self.assertNotIn('Internal accounting',str(statement))

    def test_sdk_refund_payload_has_exact_amount_reference_and_no_contact_or_note(self):
        request=self.request()
        with patch('stripe.StripeClient') as client:
            gateway=TestStripeGateway();gateway.create_refund(request)
        call=client.return_value.v1.refunds.create.call_args
        self.assertEqual(call.args[0]['amount'],3000)
        self.assertEqual(call.args[0]['payment_intent'],self.attempt.payment_intent_id)
        self.assertEqual(call.kwargs['options']['idempotency_key'],'phd-test-refund-'+str(request.pk))
        self.assertNotIn(request.note,str(call));self.assertNotIn(self.item.contact_email,str(call))
        with self.settings(NATIVE_STRIPE_TEST_REFUNDS_ENABLED=False),patch('stripe.StripeClient') as client:
            with self.assertRaises(ValidationError):TestStripeGateway().create_refund(request)
            client.return_value.v1.refunds.create.assert_not_called()

    def test_unconfirmed_adjustment_is_flagged_on_current_statement(self):
        self.gateway.refunds=[dict(self.refund(),currency='eur')]
        with self.assertRaises(ValidationError):self.sync()
        self.assertTrue(statement_for(self.account,actor=self.actor)['review_required'])
        self.gateway.refunds=[];self.sync()
        self.assertFalse(statement_for(self.account,actor=self.actor)['review_required'])


@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class RefundConcurrencyTests(RefundFixtures,TransactionTestCase):
    def setUp(self):self.seed_refunds()

    @skipUnless(connection.vendor=='postgresql','PostgreSQL locking check')
    def test_concurrent_adjustment_reads_post_once(self):
        self.gateway.refunds=[self.refund()]
        def settle(_):
            close_old_connections()
            try:return refresh(NativePaymentAttempt.objects.get(pk=self.attempt.pk),gateway=self.gateway).pk
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(settle,range(2)))
        self.assertEqual(self.bill.entries.filter(kind='refund').count(),1)
        self.assertEqual(balance(self.bill)['due'],30)

    @skipUnless(connection.vendor=='postgresql','PostgreSQL locking check')
    def test_competing_refunds_cannot_reserve_same_funds(self):
        self.bill.refresh_from_db();version=self.bill.version
        def reserve(_):
            close_old_connections()
            try:
                reserve_refund(attempt_id=self.attempt.pk,version=version,amount='80',credit_work=False,note='Concurrency test',token=uuid.uuid4(),actor=self.actor)
                return 'reserved'
            except Exception as exc:
                from .services import EditConflict
                if isinstance(exc,(ValidationError,EditConflict)):return 'blocked'
                raise
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
        self.assertCountEqual(results,['reserved','blocked']);self.assertEqual(NativeRefundRequest.objects.count(),1)
