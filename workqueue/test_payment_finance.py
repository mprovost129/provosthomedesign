import copy,uuid
from datetime import date,datetime,timedelta,timezone as utc
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.management import call_command
from django.db import connection,close_old_connections,transaction,IntegrityError
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from .test_refund_accounting import RefundFixtures,RefundGateway
from .test_online_payments import SANDBOX,session_for
from .test_payment_gateway import GatewayUnavailable,TestStripeGateway
from .test_payments import refresh
from .payment_finance_models import NativePaymentFinance,NativeBalanceEntry,NativeFinanceEvent
from .payment_finance import settings_record,save_settings,reconcile,report,begin,finish,read_entries,current,fingerprint,process_pending,cents
from .native_invoices import balance
from .native_invoice_models import NativeInvoiceEntry
from .services import EditConflict

class FinanceGateway(RefundGateway):
    def __init__(self):super().__init__();self.transactions={};self.reads=[]
    def balance_transaction(self,tid):
        self.reads.append(tid)
        if tid not in self.transactions:raise GatewayUnavailable('Fictional missing balance read')
        return copy.deepcopy(self.transactions[tid])

class FinanceFixtures(RefundFixtures):
    def seed_finance(self):
        self.seed_refunds();self.gateway.__class__=FinanceGateway;self.gateway.transactions={};self.gateway.reads=[]
        self.stamp=int(datetime(2026,10,20,16,tzinfo=utc.utc).timestamp())
        self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,320)
        self.charge()['balance_transaction']='txn_Original'
        save_settings(actor=self.actor,version=1,enabled=True)
    def charge(self):return self.gateway.sessions[self.attempt.session_id]['payment_intent']['latest_charge']
    def add_transaction(self,tid,source,kind,amount,fee,**changes):
        data={'id':tid,'object':'balance_transaction','source':source,'type':kind,'currency':'usd','exchange_rate':None,
          'amount':amount,'fee':fee,'net':amount-fee,'status':'available','created':self.stamp,'available_on':self.stamp+86400,
          'fee_details':[{'amount':fee,'currency':'usd','type':'stripe_fee'}] if fee else []}
        data.update(changes);self.gateway.transactions[tid]=data;return data
    def finance(self):return reconcile(self.attempt.pk,gateway=self.gateway)
    def totals(self,start=date(2026,10,1),end=date(2026,10,31),q=''):return report(start,end,q)
    def refund_cash(self,amount=3000,fee=0):
        obj=self.refund(amount);obj['balance_transaction']='txn_Refund';self.gateway.refunds=[obj]
        self.add_transaction('txn_Refund',obj['id'],'refund',-amount,fee);self.sync();return obj

@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class PaymentFinanceTests(FinanceFixtures,TestCase):
    def setUp(self):self.seed_finance()

    def test_actual_fees_do_not_reduce_invoice_paid_or_add_client_debt(self):
        before=balance(self.bill);count=NativeInvoiceEntry.objects.count();row=self.finance();totals=self.totals()
        self.assertTrue(current(row));self.assertEqual(totals['gross'],Decimal('100'))
        self.assertEqual(totals['fees'],Decimal('3.20'));self.assertEqual(totals['net'],Decimal('96.80'))
        self.assertEqual(balance(self.bill),before);self.assertEqual(NativeInvoiceEntry.objects.count(),count)
        self.assertEqual(balance(self.bill)['paid'],100);self.assertEqual(balance(self.bill)['due'],0)

    def test_non_percentage_provider_fee_is_used_exactly(self):
        self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,147)
        self.finance();self.assertEqual(self.totals()['net'],Decimal('98.53'))

    def test_duplicate_reads_never_duplicate_balances_or_events(self):
        self.finance();self.finance();self.finance()
        self.assertEqual(NativeBalanceEntry.objects.count(),1)
        self.assertEqual(NativeFinanceEvent.objects.filter(action='Fee history reconciled').count(),1)
        self.assertEqual(self.totals()['net'],Decimal('96.80'))

    def test_full_refund_retains_original_fee_and_net_is_negative(self):
        self.refund_cash(10000);self.finance();totals=self.totals()
        self.assertEqual(totals['gross'],100);self.assertEqual(totals['adjustments'],-100)
        self.assertEqual(totals['fees'],Decimal('3.20'));self.assertEqual(totals['net'],Decimal('-3.20'))
        self.assertEqual(balance(self.bill)['paid'],0)

    def test_partial_refund_does_not_prorate_original_fee(self):
        self.refund_cash(2500);self.finance()
        self.assertEqual(self.totals()['net'],Decimal('71.80'));self.assertEqual(self.totals()['fees'],Decimal('3.20'))

    def test_actual_fee_credit_is_signed_and_applied_once(self):
        self.refund_cash(2500,fee=-80);self.finance()
        self.assertEqual(self.totals()['fees'],Decimal('2.40'));self.assertEqual(self.totals()['net'],Decimal('72.60'))

    def test_work_credit_is_not_a_second_cash_refund(self):
        self.refund_cash(10000)
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='credit',amount=100,credit_number='TEST-CR-Extra',note='Canceled work',date=date(2026,10,20))
        self.finance();self.assertEqual(self.totals()['net'],Decimal('-3.20'));self.assertEqual(self.totals()['adjustments'],-100)

    def test_failed_refund_records_debit_and_recovery_once(self):
        obj=self.refund_cash(3000);obj['status']='failed';obj['failure_balance_transaction']='txn_Recovery'
        self.add_transaction('txn_Recovery',obj['id'],'refund_failure',3000,0);self.sync();self.finance()
        self.assertEqual(self.totals()['adjustments'],0);self.assertEqual(self.totals()['net'],Decimal('96.80'))
        self.assertEqual(NativeBalanceEntry.objects.count(),3)

    def test_failed_refund_with_no_money_movement_does_not_invent_one(self):
        self.gateway.refunds=[self.refund(3000,status='failed')];self.sync();self.finance()
        self.assertEqual(NativeBalanceEntry.objects.count(),1);self.assertEqual(self.totals()['net'],Decimal('96.80'))

    def test_failed_refund_missing_recovery_holds_net(self):
        obj=self.refund_cash();obj['status']='failed';self.sync()
        self.assertEqual(self.finance().issue,'awaiting_fees');self.assertIsNone(self.totals()['net'])

    def test_pending_refund_does_not_become_fee_free(self):
        self.gateway.refunds=[self.refund(status='pending')];self.sync()
        self.assertEqual(self.finance().issue,'awaiting_accounting');self.assertIsNone(self.totals()['fees'])

    def test_ach_payment_uses_actual_fee_and_py_charge_reference(self):
        charge=self.charge();charge['id']='py_ACHTest';self.gateway.sessions[self.attempt.session_id]['payment_method_types']=['us_bank_account']
        self.attempt.method='bank';self.attempt.save();self.add_transaction('txn_Original',charge['id'],'payment',10000,80)
        self.finance();self.assertEqual(self.totals()['fees'],Decimal('.80'));self.assertEqual(self.totals()['net'],Decimal('99.20'))

    def test_unconfirmed_bank_payment_never_gets_net(self):
        self.attempt.state='processing';self.attempt.save()
        self.assertEqual(self.finance().issue,'awaiting_accounting');self.assertIsNone(self.totals()['net'])

    def test_fee_api_failure_preserves_paid_ledger_and_old_evidence(self):
        self.finance();before=balance(self.bill);self.gateway.transactions={}
        row=self.finance();self.assertEqual(row.issue,'provider_unavailable');self.assertEqual(balance(self.bill),before)
        self.attempt.refresh_from_db();self.assertEqual(self.attempt.state,'paid');self.assertEqual(self.attempt.issue_code,'')
        self.assertEqual(NativeBalanceEntry.objects.count(),1);self.assertIsNone(self.totals()['net'])

    def test_missing_balance_reference_is_pending_not_zero(self):
        self.charge()['balance_transaction']=None
        self.assertEqual(self.finance().issue,'awaiting_fees');self.assertIsNone(self.totals()['net'])
        self.assertFalse(NativeBalanceEntry.objects.exists())

    def test_zero_fee_is_accepted_only_when_provider_explicitly_confirms_it(self):
        self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,0)
        self.finance();self.assertEqual(self.totals()['fees'],0);self.assertEqual(self.totals()['net'],100)

    def test_integer_minor_units_reject_floats_booleans_wrong_net_missing_fee(self):
        for field,value in [('amount',10000.0),('fee',True),('fee',None),('net',9681)]:
            with self.subTest(field=field,value=value):
                row=self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,320);row[field]=value
                self.assertEqual(self.finance().state,'review');self.assertFalse(NativeBalanceEntry.objects.exists())

    def test_fee_breakdown_must_equal_total_and_currency(self):
        for details in [[{'amount':319,'currency':'usd','type':'stripe_fee'}],[{'amount':320,'currency':'eur','type':'stripe_fee'}],None]:
            self.gateway.transactions['txn_Original']['fee_details']=details
            self.assertEqual(self.finance().state,'review')

    def test_source_account_currency_fx_payout_and_transaction_identity_are_checked(self):
        for changes in [{'source':'ch_SomeoneElse'},{'currency':'eur'},{'exchange_rate':1.1},{'type':'payout'},{'id':'txn_Wrong'},{'status':'lost'}]:
            with self.subTest(changes=changes):
                data=self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,320);data.update(changes)
                self.assertEqual(self.finance().state,'review');self.assertFalse(NativeBalanceEntry.objects.exists())
        self.gateway.account='acct_WrongAccount';self.assertEqual(self.finance().state,'review')

    def test_live_or_foreign_session_never_reconciles(self):
        self.gateway.sessions[self.attempt.session_id]['livemode']=True
        self.assertEqual(self.finance().state,'review');self.assertIsNone(self.totals()['net'])

    def test_new_provider_adjustment_must_first_agree_with_client_principal_ledger(self):
        self.gateway.refunds=[self.refund()]
        self.assertEqual(self.finance().issue,'awaiting_accounting');self.assertIsNone(self.totals()['net'])

    def test_complete_read_is_atomic_when_last_transaction_is_wrong(self):
        self.refund_cash();self.gateway.transactions['txn_Refund']['net']=-1
        self.assertEqual(self.finance().state,'review');self.assertEqual(NativeBalanceEntry.objects.count(),0)

    def test_original_transaction_economics_are_never_overwritten(self):
        self.finance();self.add_transaction('txn_Original',self.charge()['id'],'payment',10000,400)
        self.assertEqual(self.finance().issue,'changed_transaction');self.assertEqual(NativeBalanceEntry.objects.get().fee,320)
        self.assertIsNone(self.totals()['net'])

    def test_disappearing_previously_recorded_refund_requires_review(self):
        obj=self.refund_cash();self.finance();obj['status']='failed';obj['balance_transaction']=None;self.sync()
        self.assertEqual(self.finance().issue,'changed_transaction');self.assertEqual(NativeBalanceEntry.objects.count(),2)

    def test_duplicate_transaction_reference_is_rejected(self):
        obj=self.refund_cash();obj['balance_transaction']='txn_Original'
        self.assertEqual(self.finance().state,'review')

    def test_bank_availability_is_separate_from_net_and_payout(self):
        self.gateway.transactions['txn_Original']['status']='pending';self.finance()
        self.assertEqual(self.totals()['net'],Decimal('96.80'));self.assertEqual(self.totals()['pending_available'],Decimal('96.80'))
        self.gateway.transactions['txn_Original']['status']='available';self.finance()
        self.assertEqual(self.totals()['pending_available'],0);self.assertEqual(NativeBalanceEntry.objects.count(),1)

    def test_ledger_change_invalidates_report_without_waiting_for_a_poll(self):
        row=self.finance();self.bill.version+=1;self.bill.save()
        self.assertIsNone(self.totals()['net']);self.assertEqual(self.totals()['unresolved'],1)

    def test_adjustment_generation_race_cannot_accept_stale_fee_read(self):
        attempt,generation,stamp,adjustment=begin(self.attempt.pk);rows=read_entries(attempt,self.gateway)
        from .test_refunds import begin_read
        begin_read(attempt.pk)
        row=finish(attempt.pk,generation,stamp,adjustment,rows=rows)
        self.assertEqual(row.issue,'stale_read');self.assertFalse(NativeBalanceEntry.objects.exists())

    def test_older_fee_read_cannot_overwrite_newer_generation(self):
        first=begin(self.attempt.pk);second=begin(self.attempt.pk)
        row=finish(second[0].pk,second[1],second[2],second[3],rows=read_entries(second[0],self.gateway))
        finish(first[0].pk,first[1],first[2],first[3],issue='provider_unavailable')
        row.refresh_from_db();self.assertEqual(row.state,'verified');self.assertEqual(row.issue,'')

    def test_refund_reports_in_its_own_month_not_payment_month(self):
        self.refund_cash();self.gateway.transactions['txn_Refund']['created']=int(datetime(2026,11,10,15,tzinfo=utc.utc).timestamp())
        self.finance();october=self.totals();november=self.totals(date(2026,11,1),date(2026,11,30))
        self.assertEqual(october['net'],Decimal('96.80'));self.assertEqual(november['gross'],0)
        self.assertEqual(november['net'],-30);self.assertEqual(november['fees'],0)

    def test_eastern_midnight_and_dst_period_boundaries(self):
        # November 1 is a 25-hour local day. 04:00Z is its start, 05:00Z next day is its end.
        for stamp,included in [(datetime(2026,11,1,3,59,tzinfo=utc.utc),False),(datetime(2026,11,1,4,tzinfo=utc.utc),True),
          (datetime(2026,11,2,4,59,tzinfo=utc.utc),True),(datetime(2026,11,2,5,tzinfo=utc.utc),False)]:
            with self.subTest(stamp=stamp):
                NativeBalanceEntry.objects.all().delete();self.gateway.transactions['txn_Original']['created']=int(stamp.timestamp())
                self.finance();self.assertEqual(self.totals(date(2026,11,1),date(2026,11,1))['gross'],100 if included else 0)

    def test_pending_outside_date_range_still_withholds_misleading_complete_net(self):
        self.assertIsNone(self.totals(date(2026,1,1),date(2026,1,2))['net'])

    def test_search_scope_is_explicit_and_excludes_unrelated_payments(self):
        self.finance();self.assertEqual(self.totals(q=self.bill.number)['net'],Decimal('96.80'))
        self.assertEqual(self.totals(q='Definitely absent')['gross'],0)

    def test_reporting_and_get_views_never_call_stripe_or_change_accounting(self):
        with patch('workqueue.payment_finance.TestStripeGateway',side_effect=AssertionError('No GET provider call')):
            response=self.client.get(reverse('workqueue:payment_finance'));self.assertEqual(response.status_code,200)
            self.assertContains(response,'Pending reconciliation');self.assertIn('no-store',response['Cache-Control'])
            response=self.client.get(reverse('workqueue:payment_finance_detail',args=[self.attempt.pk]));self.assertEqual(response.status_code,200)
        self.assertFalse(NativeBalanceEntry.objects.exists())

    def test_settings_owner_only_versioned_and_default_off(self):
        from .payment_finance_models import NativeFinanceSettings
        NativeFinanceSettings.objects.all().delete();self.assertFalse(settings_record().enabled)
        self.assertEqual(process_pending()['checked'],0)
        with self.assertRaises(ValidationError):self.finance()
        self.actor.is_superuser=False
        with self.assertRaises(PermissionDenied):save_settings(actor=self.actor,version=1,enabled=True)
        self.actor.is_superuser=True
        with self.assertRaises(EditConflict):save_settings(actor=self.actor,version=999,enabled=True)

    def test_anonymous_nonstaff_csrf_and_preview_cannot_refresh_financial_data(self):
        url=reverse('workqueue:payment_finance_detail',args=[self.attempt.pk])
        anonymous=Browser();self.assertEqual(anonymous.get(url).status_code,302);self.assertEqual(anonymous.post(url).status_code,302)
        protected=Browser(enforce_csrf_checks=True);protected.force_login(self.actor);self.assertEqual(protected.post(url).status_code,403)
        with self.settings(WORK_QUEUE_ENABLED=False,WORK_QUEUE_STAFF_PREVIEW=True), patch('workqueue.payment_finance_views.reconcile',side_effect=AssertionError('No mutation')):
            self.assertEqual(self.client.post(url).status_code,403)

    def test_filter_invalid_range_is_a_clear_error(self):
        url=reverse('workqueue:payment_finance')
        for params in [{'start':'oops','end':'2026-10-20'},{'start':'2026-11-01','end':'2026-10-01'},
          {'start':'2024-01-01','end':'2026-10-01'}]:self.assertEqual(self.client.get(url,params).status_code,400)
        with self.assertRaises(ValidationError):self.totals(date(2026,11,1),date(2026,10,1))

    def test_refresh_ui_displays_dollars_and_suppresses_old_summary(self):
        self.finance();url=reverse('workqueue:payment_finance_detail',args=[self.attempt.pk])
        response=self.client.get(url);self.assertContains(response,'$96.80');self.assertContains(response,'txn_Original')
        self.gateway.transactions={};self.finance();response=self.client.get(url)
        self.assertContains(response,'Historical evidence only');self.assertContains(response,'Pending')

    def test_command_bound_and_disabled_path_makes_no_network_calls(self):
        with patch('workqueue.payment_finance.TestStripeGateway',return_value=self.gateway):
            output=StringIO();call_command('reconcile_payment_fees',limit=1,stdout=output)
            self.assertIn('checks: 1',output.getvalue());self.assertEqual(len(self.gateway.reads),1)
            call_command('reconcile_payment_fees',limit=1,stdout=output);self.assertEqual(len(self.gateway.reads),1)

    def test_reversed_original_payment_is_a_ledger_mismatch_not_fee_income(self):
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='reversal',amount=self.attempt.amount,reverses=self.attempt.ledger_entry,note='Correction',date=date(2026,10,20))
        self.assertEqual(self.finance().state,'review');self.assertIsNone(self.totals()['net'])

    def test_normal_read_only_staff_can_view_but_not_refresh_or_enable(self):
        user=get_user_model().objects.create_user('finance-viewer',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        browser=Browser();browser.force_login(user)
        self.assertEqual(browser.get(reverse('workqueue:payment_finance')).status_code,200)
        self.assertEqual(browser.post(reverse('workqueue:payment_finance_detail',args=[self.attempt.pk])).status_code,403)
        self.assertEqual(browser.post(reverse('workqueue:payment_finance'),{'version':1,'enabled':'on'}).status_code,403)

    def test_search_and_pagination_keep_default_period(self):
        response=self.client.get(reverse('workqueue:payment_finance'))
        self.assertIn('start=',response.context['query_string']);self.assertIn('end=',response.context['query_string'])
        self.finance();response=self.client.get(reverse('workqueue:payment_finance'))
        self.assertContains(response,'Reconciled snapshots checked');self.assertContains(response,'$96.80')

    def test_pending_refund_failure_does_not_change_paid_status_on_fee_error(self):
        obj=self.refund_cash();obj['status']='failed';self.sync();before=balance(self.bill)
        self.assertEqual(self.finance().issue,'awaiting_fees');self.assertEqual(balance(self.bill),before)
        self.assertEqual(balance(self.bill)['paid'],100)

    def test_database_constraint_rejects_bad_net(self):
        self.finance()
        with self.assertRaises(IntegrityError),transaction.atomic():NativeBalanceEntry.objects.update(net=100)

    def test_provider_dispute_fee_and_recovery_are_separate_from_principal(self):
        dispute=self.dispute(status='lost',amounts=(-4000,));self.gateway.disputes=[dispute];self.charge()['disputed']=True
        self.add_transaction('txn_0',dispute['id'],'adjustment',-4000,1500);self.sync();self.finance()
        self.assertEqual(self.totals()['adjustments'],-40);self.assertEqual(self.totals()['fees'],Decimal('18.20'))
        self.assertEqual(self.totals()['net'],Decimal('41.80'))
        dispute['status']='won';dispute['balance_transactions'].append({'id':'txn_1','object':'balance_transaction','currency':'usd','amount':4000})
        self.add_transaction('txn_1',dispute['id'],'adjustment',4000,-1500);self.sync();self.finance()
        self.assertEqual(self.totals()['adjustments'],0);self.assertEqual(self.totals()['net'],Decimal('96.80'))

@skipUnless(connection.vendor=='postgresql','Accounting concurrency requires PostgreSQL')
@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class ConcurrentFinanceTests(FinanceFixtures,TransactionTestCase):
    def setUp(self):self.seed_finance()
    def test_concurrent_reads_apply_only_once(self):
        def run():
            close_old_connections()
            try:return reconcile(self.attempt.pk,gateway=self.gateway).state
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(NativeBalanceEntry.objects.count(),1)
        self.assertEqual(self.totals()['net'],Decimal('96.80'))
