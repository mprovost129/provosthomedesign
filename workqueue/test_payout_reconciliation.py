import copy,uuid
from datetime import date,datetime,timedelta,timezone as utc
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch,MagicMock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.management import call_command
from django.db import connection,close_old_connections,transaction,IntegrityError
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from .test_online_payments import SANDBOX
from .test_payment_finance import FinanceFixtures,FinanceGateway
from .test_payment_gateway import TestStripeGateway,GatewayUnavailable
from .native_invoices import balance
from .native_invoice_models import NativeInvoiceEntry
from .payment_finance import reconcile as reconcile_fees
from .payment_finance_models import NativeBalanceEntry
from .payout_models import NativePayoutSettings,NativePayout,NativePayoutTransaction,NativeBankConfirmation,NativePayoutEvent
from .payout_reconciliation import (settings_record,save_settings,discover,lookup,reconcile,begin,finish,read_payout,
    stripe_balanced,local_links,bank_state,confirm_bank,undo_bank,process_pending,PayoutIssue)
from .services import EditConflict

class PayoutGateway(FinanceGateway):
    def payout(self,pid):return copy.deepcopy(self.payouts[pid])
    def recent_payouts(self,since):return copy.deepcopy(list(self.payouts.values()))
    def payout_transactions(self,pid):return copy.deepcopy(self.members[pid])

class PayoutFixtures(FinanceFixtures):
    def seed_payouts(self):
        self.seed_finance();self.finance();self.gateway.__class__=PayoutGateway
        self.gateway.payouts={};self.gateway.members={};self.pid='po_FictionalBankTransfer'
        self.payout_obj={'id':self.pid,'object':'payout','livemode':False,'amount':9680,'currency':'usd','status':'paid',
          'automatic':True,'method':'standard','reconciliation_status':'completed','created':self.stamp+86400,
          'arrival_date':self.stamp+172800,'balance_transaction':'txn_PayoutDebit','failure_balance_transaction':None,
          'original_payout':None,'reversed_by':None,'destination':'ba_DoNotStoreAccount','description':'Do not persist raw private text'}
        self.gateway.payouts[self.pid]=self.payout_obj
        self.add_transaction('txn_PayoutDebit',self.pid,'payout',-9680,0)
        self.gateway.members[self.pid]=[copy.deepcopy(self.gateway.transactions['txn_Original'])]
        save_settings(actor=self.actor,version=1,enabled=True)
        self.row=discover(gateway=self.gateway)[0]
    def payout_sync(self):return reconcile(self.row.pk,gateway=self.gateway)
    def bank(self,**changes):
        self.row.refresh_from_db()
        args=dict(payout_id=self.row.pk,actor=self.actor,version=self.row.version,amount='96.80',deposit_date=date(2026,10,22),reference='Fictional statement line 1',token=uuid.uuid4(),confirm=True)
        args.update(changes);return confirm_bank(**args)
    def member(self,tid,source,kind,amount,fee=0):
        data=self.add_transaction(tid,source,kind,amount,fee);self.gateway.members[self.pid].append(copy.deepcopy(data));return data
    def set_payout_amount(self,amount,fee=0):
        self.payout_obj['amount']=amount
        self.add_transaction('txn_PayoutDebit',self.pid,'payout',-amount,fee)
        self.row.amount=amount;self.row.provider_snapshot['amount']=amount;self.row.save()

@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class PayoutReconciliationTests(PayoutFixtures,TestCase):
    def setUp(self):self.seed_payouts()

    def test_exact_payout_math_links_invoice_and_never_creates_more_revenue(self):
        before=balance(self.bill);count=NativeInvoiceEntry.objects.count();result=self.payout_sync()
        self.assertTrue(stripe_balanced(result));self.assertEqual(result.debit_net,-9680)
        links=local_links(result);self.assertEqual(links['local_net'],Decimal('96.80'));self.assertEqual(links['unlinked_net'],0)
        self.assertEqual(links['local_issues'],0);self.assertEqual(bank_state(result),'unconfirmed')
        self.assertEqual(balance(self.bill),before);self.assertEqual(NativeInvoiceEntry.objects.count(),count)

    def test_payout_debit_is_not_subtracted_twice_when_provider_list_includes_it(self):
        self.gateway.members[self.pid].append(copy.deepcopy(self.gateway.transactions['txn_PayoutDebit']))
        row=self.payout_sync();self.assertTrue(stripe_balanced(row));self.assertEqual(row.transactions.count(),1)

    def test_original_processing_fee_is_not_deducted_again_from_net(self):
        row=self.payout_sync();self.assertEqual(sum(t.net for t in row.transactions.all()),9680)
        self.assertEqual(row.amount,9680);self.assertEqual(row.debit_fee,0)

    def test_distinct_payout_fee_is_applied_once_and_conservation_holds(self):
        self.set_payout_amount(9670,fee=10);row=self.payout_sync()
        self.assertTrue(stripe_balanced(row));self.assertEqual(row.debit_fee,10)
        self.assertEqual(sum(t.net for t in row.transactions.all())-row.debit_fee,row.amount)

    def test_partial_refund_and_fee_credit_balance_in_the_same_payout(self):
        self.member('txn_RefundPayout','re_Original','refund',-3000,-80)
        self.set_payout_amount(6760);row=self.payout_sync();self.assertTrue(stripe_balanced(row))
        self.assertEqual(sum(t.fee for t in row.transactions.all()),240)
        self.assertEqual(local_links(row)['unlinked_net'],Decimal('-29.20'))

    def test_other_stripe_activity_is_not_falsely_assigned_to_an_app_invoice(self):
        self.member('txn_OtherSale','ch_OtherBusinessPayment','charge',1000,59)
        self.set_payout_amount(10621);row=self.payout_sync()
        self.assertTrue(stripe_balanced(row));self.assertEqual(local_links(row)['local_net'],Decimal('96.80'))
        self.assertEqual(local_links(row)['unlinked_net'],Decimal('9.41'))

    def test_standalone_stripe_fee_is_a_balance_movement_not_an_extra_percent_estimate(self):
        self.member('txn_MonthlyFee',None,'stripe_fee',-100,0)
        # A standalone fee may have no source object.
        self.gateway.members[self.pid][-1]['source']=None
        self.set_payout_amount(9580);row=self.payout_sync();self.assertTrue(stripe_balanced(row))
        self.assertEqual(sum(t.net for t in row.transactions.all()),9580)

    def test_repeated_reads_do_not_duplicate_transactions_or_balanced_events(self):
        self.payout_sync();self.payout_sync();self.payout_sync()
        self.assertEqual(NativePayoutTransaction.objects.count(),1)
        self.assertEqual(NativePayoutEvent.objects.filter(action='Stripe payout balanced').count(),1)

    def test_one_cent_difference_withholds_balanced_and_bank_confirmation(self):
        self.gateway.transactions['txn_PayoutDebit']['amount']=-9681;self.gateway.transactions['txn_PayoutDebit']['net']=-9681
        row=self.payout_sync();self.assertEqual(row.state,'review');self.assertFalse(stripe_balanced(row))
        with self.assertRaises(ValidationError):self.bank()
        self.assertFalse(row.transactions.exists())

    def test_invalid_net_or_fee_breakdown_is_not_accepted(self):
        for key,value in [('net',9679),('fee',None),('amount',10000.0),('fee',True),('fee_details',[])]:
            with self.subTest(key=key,value=value):
                original=copy.deepcopy(self.gateway.members[self.pid]);self.gateway.members[self.pid][0][key]=value
                row=self.payout_sync();self.assertEqual(row.state,'review');self.assertEqual(row.transactions.count(),0)
                self.gateway.members[self.pid]=original

    def test_malformed_payout_source_currency_or_identity_is_rejected(self):
        for changes in [{'source':'po_SomeoneElse'},{'currency':'eur'},{'type':'charge'},{'id':'txn_WrongDebit'},{'exchange_rate':1.25}]:
            original=copy.deepcopy(self.gateway.transactions['txn_PayoutDebit']);self.gateway.transactions['txn_PayoutDebit'].update(changes)
            self.assertFalse(stripe_balanced(self.payout_sync()));self.gateway.transactions['txn_PayoutDebit']=original

    def test_duplicate_transaction_and_other_payout_debit_are_rejected(self):
        self.gateway.members[self.pid].append(copy.deepcopy(self.gateway.members[self.pid][0]))
        self.assertEqual(self.payout_sync().state,'review');self.gateway.members[self.pid].pop()
        self.member('txn_ForeignPayout','po_Other','payout',-100,0)
        self.assertEqual(self.payout_sync().state,'review')

    def test_pending_or_in_transit_is_not_a_confirmed_bank_receipt(self):
        for status in ['pending','in_transit']:
            self.payout_obj['status']=status;row=self.payout_sync()
            self.assertEqual(row.issue,'not_paid');self.assertFalse(stripe_balanced(row));self.assertEqual(bank_state(row),'unconfirmed')
            with self.assertRaises(ValidationError):self.bank()

    def test_stripe_reconciliation_in_progress_does_not_guess_membership(self):
        self.payout_obj['reconciliation_status']='in_progress'
        row=self.payout_sync();self.assertEqual(row.issue,'not_ready');self.assertFalse(row.transactions.exists())

    def test_manual_instant_reversed_and_foreign_currency_require_separate_review(self):
        for changes in [{'automatic':False},{'method':'instant'},{'reversed_by':'po_Reverse'},{'currency':'eur'}]:
            with self.subTest(changes=changes):
                original=copy.deepcopy(self.payout_obj);self.payout_obj.update(changes)
                if changes.get('currency'):self.row.currency='eur';self.row.save()
                row=self.payout_sync();self.assertEqual(row.issue,'unsupported');self.assertFalse(stripe_balanced(row))
                self.payout_obj.clear();self.payout_obj.update(original);self.row.currency='usd';self.row.save()

    def test_live_payouts_and_wrong_sandbox_account_are_rejected(self):
        self.payout_obj['livemode']=True;self.assertEqual(self.payout_sync().state,'review')
        self.payout_obj['livemode']=False;self.gateway.account='acct_Different';self.assertEqual(self.payout_sync().state,'review')

    def test_unknown_or_incomplete_history_never_looks_balanced(self):
        for raw in [[],None,{}]:
            self.gateway.members[self.pid]=raw
            self.assertFalse(stripe_balanced(self.payout_sync()))

    def test_provider_failure_preserves_previous_evidence_but_blocks_current_confirmation(self):
        self.payout_sync();self.gateway.payout_transactions=lambda pid:(_ for _ in ()).throw(GatewayUnavailable('Fictional outage'))
        row=self.payout_sync();self.assertEqual(row.issue,'provider_unavailable');self.assertEqual(row.transactions.count(),1)
        self.assertFalse(stripe_balanced(row));self.assertEqual(balance(self.bill)['paid'],100)

    def test_previously_verified_economics_are_not_overwritten(self):
        self.payout_sync();item=self.gateway.members[self.pid][0];item['amount']+=1;item['fee']+=1;item['fee_details'][0]['amount']+=1
        row=self.payout_sync();self.assertEqual(row.issue,'changed_history')
        self.assertEqual(row.transactions.get().amount,10000);self.assertEqual(row.transactions.get().fee,320)

    def test_missing_previously_verified_transactions_requires_review(self):
        self.payout_sync();self.gateway.members[self.pid]=[]
        self.assertFalse(stripe_balanced(self.payout_sync()));self.assertEqual(NativePayoutTransaction.objects.count(),1)

    def test_payment_or_payout_metadata_amount_change_is_not_silently_accepted(self):
        self.payout_sync();self.payout_obj['amount']=9681
        row=self.payout_sync();self.assertEqual(row.issue,'changed_history');self.assertEqual(row.amount,9680)

    def test_older_provider_read_cannot_overwrite_a_newer_generation(self):
        first=begin(self.row.pk);second=begin(self.row.pk);data,rows,debit,issue=read_payout(second[0],self.gateway)
        finish(second[0].pk,second[1],second[2],data=data,rows=rows,debit=debit,issue=issue)
        row=finish(first[0].pk,first[1],first[2],issue='provider_unavailable');self.assertTrue(stripe_balanced(row))

    def test_bank_confirmation_or_other_version_change_blocks_a_stale_provider_read(self):
        first=begin(self.row.pk);self.row.refresh_from_db();self.row.version+=1;self.row.save(update_fields=['version'])
        data,rows,debit,issue=read_payout(first[0],self.gateway)
        row=finish(first[0].pk,first[1],first[2],data=data,rows=rows,debit=debit)
        self.assertEqual(row.issue,'stale_read');self.assertFalse(row.transactions.exists())

    def test_same_balance_transaction_cannot_reconcile_two_paid_payouts(self):
        self.payout_sync();second=copy.deepcopy(self.payout_obj);second['id']='po_Another';second['balance_transaction']='txn_AnotherPayout'
        self.gateway.payouts[second['id']]=second;self.gateway.members[second['id']]=copy.deepcopy(self.gateway.members[self.pid])
        self.add_transaction('txn_AnotherPayout',second['id'],'payout',-9680,0)
        row=lookup(second['id'],gateway=self.gateway);self.assertEqual(row.issue,'duplicate_membership')

    def test_failed_old_payout_can_release_membership_for_a_real_retry(self):
        self.payout_sync();self.payout_obj['status']='failed';self.payout_sync()
        second=copy.deepcopy(self.payout_obj);second.update(id='po_Retry',status='paid',balance_transaction='txn_RetryDebit')
        self.gateway.payouts[second['id']]=second;self.gateway.members[second['id']]=copy.deepcopy(self.gateway.members[self.pid]);self.add_transaction('txn_RetryDebit',second['id'],'payout',-9680,0)
        row=lookup(second['id'],gateway=self.gateway);self.assertTrue(stripe_balanced(row))

    def test_bank_confirmation_requires_exact_amount_statement_date_and_owner(self):
        self.payout_sync();before=balance(self.bill);count=NativeInvoiceEntry.objects.count();record=self.bank()
        self.row.refresh_from_db();self.assertEqual(bank_state(self.row),'confirmed');self.assertEqual(record.amount,9680)
        self.assertEqual(balance(self.bill),before);self.assertEqual(NativeInvoiceEntry.objects.count(),count)
        self.assertEqual(self.attempt.amount,100)

    def test_incorrect_bank_amount_never_creates_a_matching_record(self):
        self.payout_sync()
        for amount in ['96.79','96.81','96.801','NaN','Infinity',True,'0','-1']:
            with self.subTest(amount=amount),self.assertRaises(ValidationError):self.bank(amount=amount)
        self.assertFalse(NativeBankConfirmation.objects.exists())

    def test_bank_date_reference_explicit_confirmation_and_freshness_are_required(self):
        self.payout_sync()
        for changes in [{'deposit_date':date(2030,1,1)},{'deposit_date':date(2020,1,1)},{'reference':''},{'confirm':False}]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.bank(**changes)
        NativePayout.objects.filter(pk=self.row.pk).update(checked_at=timezone.now()-timedelta(hours=2))
        with self.assertRaises(ValidationError):self.bank()

    def test_confirmation_replay_is_idempotent_and_different_replay_is_rejected(self):
        self.payout_sync();token=uuid.uuid4();self.bank(token=token);self.bank(token=token)
        self.assertEqual(NativeBankConfirmation.objects.count(),1)
        with self.assertRaises(ValidationError):self.bank(token=token,reference='Changed reference')
        with self.assertRaises(ValidationError):self.bank()

    def test_stale_bank_confirmation_is_rejected(self):
        self.payout_sync()
        with self.assertRaises(EditConflict):self.bank(version=1)

    def test_failed_payout_invalidates_prior_bank_confirmation_without_deleting_history(self):
        self.payout_sync();self.bank();self.payout_obj['status']='failed';row=self.payout_sync()
        self.assertEqual(row.issue,'payout_failed');self.assertEqual(bank_state(row),'review')
        self.assertEqual(NativeBankConfirmation.objects.count(),1);self.assertEqual(balance(self.bill)['due'],0)

    def test_bank_correction_retains_old_record_and_can_record_replacement(self):
        self.payout_sync();record=self.bank();self.row.refresh_from_db()
        undo_bank(payout_id=self.row.pk,actor=self.actor,version=self.row.version,reason='Fictional correction')
        self.row.refresh_from_db();record.refresh_from_db();self.assertEqual(bank_state(self.row),'unconfirmed')
        self.assertIsNotNone(record.revoked_at);self.bank(reference='Correct fictional line')
        self.assertEqual(NativeBankConfirmation.objects.count(),2)

    def test_nonowner_staff_cannot_confirm_correct_or_change_settings(self):
        self.payout_sync();self.actor.is_superuser=False
        for callback in [lambda:self.bank(),lambda:undo_bank(payout_id=self.row.pk,actor=self.actor,version=self.row.version,reason='No'),lambda:save_settings(actor=self.actor,version=2,enabled=False)]:
            with self.assertRaises(PermissionDenied):callback()

    def test_pending_linked_invoice_costs_block_confirmation_but_not_provider_math(self):
        self.payout_sync();NativeBalanceEntry.objects.get().finance.__class__.objects.update(state='pending')
        self.assertTrue(stripe_balanced(self.payout_sync()));self.assertEqual(local_links(self.row)['local_issues'],1)
        with self.assertRaises(ValidationError):self.bank()

    def test_link_source_and_account_are_verified_not_just_equal_amounts(self):
        self.payout_sync();NativeBalanceEntry.objects.update(source_id='ch_WrongSource')
        self.assertEqual(local_links(self.row)['local_issues'],1)
        with self.assertRaises(ValidationError):self.bank()

    def test_saved_transaction_corruption_cannot_remain_balanced_even_if_net_sum_matches(self):
        self.payout_sync();NativePayoutTransaction.objects.update(amount=10001,fee=321)
        self.row.refresh_from_db();self.assertFalse(stripe_balanced(self.row))
        with self.assertRaises(ValidationError):self.bank()

    def test_raw_bank_destination_and_description_are_not_stored(self):
        self.payout_sync();self.row.refresh_from_db();serialized=str(self.row.provider_snapshot)
        self.assertNotIn('ba_DoNotStoreAccount',serialized);self.assertNotIn('Do not persist',serialized)

    def test_discovery_is_atomic_and_duplicate_or_foreign_rows_do_not_partially_import(self):
        original=copy.deepcopy(self.payout_obj);foreign=copy.deepcopy(original);foreign.update(id='po_Foreign',livemode=True)
        self.gateway.payouts[foreign['id']]=foreign
        with self.assertRaises(ValidationError):discover(gateway=self.gateway)
        self.assertEqual(NativePayout.objects.count(),1)
        self.assertTrue(settings_record().discovery_issue)

    def test_settings_default_off_and_commands_never_contact_provider_while_paused(self):
        NativePayoutSettings.objects.all().delete();self.assertFalse(settings_record().enabled)
        with patch('workqueue.payout_reconciliation.TestStripeGateway',side_effect=AssertionError('No provider call')):
            self.assertEqual(process_pending()['checked'],0)
            with self.assertRaises(ValidationError):self.payout_sync()

    def test_hourly_discovery_and_fifteen_minute_transaction_checks_are_bounded(self):
        with patch('workqueue.payout_reconciliation.TestStripeGateway',return_value=self.gateway):
            first=process_pending(limit=1);second=process_pending(limit=1)
        self.assertEqual(first['checked'],1);self.assertEqual(second['checked'],0)
        self.assertEqual(first['discovered'],0)

    def test_unknown_payout_lookup_verifies_exact_reference(self):
        row=lookup(self.pid,gateway=self.gateway);self.assertEqual(row.pk,self.row.pk)
        with self.assertRaises(ValidationError):lookup('https://example.com/po_Whatever',gateway=self.gateway)

    def test_get_views_are_private_and_never_call_provider_or_create_bank_records(self):
        self.payout_sync()
        with patch('workqueue.payout_reconciliation.TestStripeGateway',side_effect=AssertionError('No GET network')):
            response=self.client.get(reverse('workqueue:payouts'));self.assertEqual(response.status_code,200)
            self.assertContains(response,'$96.80');self.assertContains(response,'Not confirmed');self.assertIn('no-store',response['Cache-Control'])
            response=self.client.get(reverse('workqueue:payout_detail',args=[self.row.pk]));self.assertEqual(response.status_code,200)
            self.assertContains(response,'Invoice payment agrees');self.assertContains(response,'Record a fictional bank receipt')
        self.assertFalse(NativeBankConfirmation.objects.exists())

    def test_anonymous_csrf_readonly_staff_and_preview_are_blocked_from_mutations(self):
        url=reverse('workqueue:payout_detail',args=[self.row.pk]);anonymous=Browser()
        self.assertEqual(anonymous.get(url).status_code,302);self.assertEqual(anonymous.post(url).status_code,302)
        protected=Browser(enforce_csrf_checks=True);protected.force_login(self.actor);self.assertEqual(protected.post(url).status_code,403)
        user=get_user_model().objects.create_user('payout-viewer',is_staff=True);user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        viewer=Browser();viewer.force_login(user);self.assertEqual(viewer.get(url).status_code,200);self.assertEqual(viewer.post(url,{'action':'refresh'}).status_code,403)
        with self.settings(WORK_QUEUE_ENABLED=False,WORK_QUEUE_STAFF_PREVIEW=True):self.assertEqual(self.client.post(url,{'action':'refresh'}).status_code,403)

    def test_filter_range_and_bank_amount_errors_are_visible(self):
        url=reverse('workqueue:payouts')
        self.assertEqual(self.client.get(url,{'start':'2026-10-23','end':'2026-10-01'}).status_code,400)
        self.payout_sync();self.row.refresh_from_db();url=reverse('workqueue:payout_detail',args=[self.row.pk])
        response=self.client.post(url,{'action':'confirm','version':self.row.version,'token':str(uuid.uuid4()),'amount':'96.79','deposit_date':'2026-10-22','reference':'Fictional line','confirm':'on'})
        self.assertEqual(response.status_code,400);self.assertContains(response,'differs',status_code=400)

    def test_gateway_reads_the_complete_payout_scope_and_no_write_endpoint(self):
        with patch('stripe.StripeClient') as client:
            gateway=TestStripeGateway();gateway.client=client.return_value
            gateway.client.v1.payouts.list.return_value.auto_paging_iter.return_value=iter([copy.deepcopy(self.payout_obj)])
            self.assertEqual(len(gateway.recent_payouts(timezone.now())),1)
            gateway.client.v1.balance_transactions.list.return_value.auto_paging_iter.return_value=iter([])
            self.assertEqual(gateway.payout_transactions(self.pid),[])
            self.assertEqual(gateway.client.v1.balance_transactions.list.call_args.args[0],{'payout':self.pid,'limit':100})
            gateway.client.v1.payouts.create.assert_not_called();gateway.client.v1.payouts.cancel.assert_not_called()

    def test_gateway_rejects_truncated_history_instead_of_accepting_first_page(self):
        with patch('stripe.StripeClient') as client:
            gateway=TestStripeGateway();gateway.client=client.return_value
            gateway.client.v1.payouts.list.return_value.auto_paging_iter.return_value=iter([{}]*251)
            with self.assertRaises(GatewayUnavailable):gateway.recent_payouts(timezone.now())
            gateway.client.v1.balance_transactions.list.return_value.auto_paging_iter.return_value=iter([{}]*1001)
            with self.assertRaises(GatewayUnavailable):gateway.payout_transactions(self.pid)

    def test_database_rejects_bad_transaction_and_debit_math(self):
        self.payout_sync()
        with self.assertRaises(IntegrityError),transaction.atomic():NativePayoutTransaction.objects.update(net=1)
        with self.assertRaises(IntegrityError),transaction.atomic():NativePayout.objects.update(debit_net=1)
        with self.assertRaises(IntegrityError),transaction.atomic():NativePayout.objects.update(debit_fee=None)

    def test_worker_command_is_bounded_and_part_of_existing_job(self):
        with patch('workqueue.payout_reconciliation.TestStripeGateway',return_value=self.gateway):
            output=StringIO();call_command('reconcile_test_payouts',limit=1,stdout=output);self.assertIn('payout checks: 1',output.getvalue())
        with patch('workqueue.management.commands.run_queue_jobs.call_command') as dispatch,patch('workqueue.health.worker_started',return_value=uuid.uuid4()),patch('workqueue.health.worker_finished'):
            call_command('run_queue_jobs',stdout=StringIO())
            self.assertIn('reconcile_test_payouts',[call.args[0] for call in dispatch.call_args_list])

@skipUnless(connection.vendor=='postgresql','Payout concurrency requires PostgreSQL')
@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class ConcurrentPayoutTests(PayoutFixtures,TransactionTestCase):
    def setUp(self):self.seed_payouts();self.payout_sync()
    def test_concurrent_confirmation_is_exactly_once(self):
        self.row.refresh_from_db();version=self.row.version
        def run():
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=self.actor.pk)
                try:return confirm_bank(payout_id=self.row.pk,actor=actor,version=version,amount='96.80',deposit_date=date(2026,10,22),reference='Fictional line',token=uuid.uuid4(),confirm=True).pk
                except (EditConflict,ValidationError):return None
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(sum(value is not None for value in results),1);self.assertEqual(NativeBankConfirmation.objects.count(),1)
    def test_concurrent_provider_reads_cannot_duplicate_payout_or_invoice_money(self):
        before=balance(self.bill)
        def run():
            close_old_connections()
            try:return reconcile(self.row.pk,gateway=self.gateway).state
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(NativePayoutTransaction.objects.count(),1);self.assertEqual(balance(self.bill),before)
