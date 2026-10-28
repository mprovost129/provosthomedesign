import copy
import uuid
from datetime import date, datetime, timedelta, timezone as utc
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .test_payment_finance import FinanceFixtures, FinanceGateway
from .test_online_payments import SANDBOX
from .test_payment_gateway import TestStripeGateway, GatewayUnavailable
from .monthly_finance_models import NativeMonthState, NativeMonthSnapshot, NativeFinancialReview
from .monthly_finance import refresh_month, report, save_review, saved_report, month_dates, bounds, digest
from .monthly_finance_views import export_csv
from .native_invoice_models import NativeInvoiceEntry
from .native_invoices import balance
from .payment_finance_models import NativeBalanceEntry
from .services import EditConflict


class MonthlyGateway(FinanceGateway):
    def month_activity(self, first, last):
        return copy.deepcopy([row for row in self.transactions.values() if first.timestamp()<=row['created']<last.timestamp()])


@override_settings(**SANDBOX,NATIVE_STRIPE_TEST_REFUNDS_ENABLED=True)
class MonthlyReviewTests(FinanceFixtures,TestCase):
    def setUp(self):
        self.seed_finance()
        NativeInvoiceEntry.objects.filter(pk=self.attempt.ledger_entry_id).update(date=date(2026,10,20))
        self.finance()
        self.gateway.__class__=MonthlyGateway
        self.clock=patch('django.utils.timezone.now',return_value=datetime(2026,11,15,17,tzinfo=utc.utc))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.client.force_login(self.actor)
        self.url=reverse('workqueue:monthly_finance')

    def refresh(self,month='2026-10',token=None):
        return refresh_month(month=month,token=token or uuid.uuid4(),actor=self.actor,gateway=self.gateway)

    def review(self,**changes):
        args={'month':'2026-10','token':uuid.uuid4(),'actor':self.actor,'note':'Reviewed fictional activity against provider evidence.',
            'confirm':True,'fingerprint':report('2026-10')['fingerprint']}
        args.update(changes);return save_review(**args)

    def test_monthly_fees_and_payout_are_exact_without_counting_transfer_as_revenue(self):
        self.add_transaction('txn_AccountCost',None,'stripe_fee',-150,9)
        self.add_transaction('txn_PaidOut','po_Test','payout',-9521,0)
        count=NativeInvoiceEntry.objects.count();before=balance(self.bill)
        self.refresh();data=report('2026-10');total=data['totals']
        self.assertEqual(total['stripe_payments'],10000);self.assertEqual(total['stripe_costs'],479)
        self.assertEqual(total['net_before_payouts'],9521);self.assertEqual(total['payout_movements'],-9521)
        self.assertEqual(total['stripe_balance_movement'],0);self.assertEqual(data['issues'],[])
        self.assertEqual(count,NativeInvoiceEntry.objects.count());self.assertEqual(before,balance(self.bill))

    def test_refund_and_fee_credit_are_signed_not_estimated(self):
        self.refund_cash(2500,fee=-80);self.finance();self.refresh()
        total=report('2026-10')['totals']
        self.assertEqual(total['stripe_refunds'],-2500);self.assertEqual(total['stripe_costs'],240)
        self.assertEqual(total['net_before_payouts'],7260)

    def test_separate_service_cost_is_included_even_without_a_saved_payout(self):
        self.add_transaction('txn_UnpaidAccountCost',None,'stripe_fee',-150,9)
        self.refresh();self.assertEqual(report('2026-10')['totals']['stripe_costs'],479)

    def test_payout_fee_counted_once(self):
        self.add_transaction('txn_Payout','po_Fee','payout',-9670,10);self.refresh()
        total=report('2026-10')['totals'];self.assertEqual(total['stripe_costs'],330)
        self.assertEqual(total['stripe_balance_movement'],0)

    def test_fee_credit_reduces_costs_and_other_movement_is_not_called_revenue(self):
        self.add_transaction('txn_FeeCredit',None,'stripe_fee',50,0)
        self.add_transaction('txn_Unknown',None,'adjustment',1000,0)
        self.refresh();data=report('2026-10')
        self.assertEqual(data['totals']['stripe_costs'],270);self.assertEqual(data['totals']['other_movements'],1000)
        self.assertTrue(any('Other Stripe' in text for text in data['issues']))
        with self.assertRaises(ValidationError):self.review()

    def test_refunded_payment_keeps_fee_and_reopens_receivable(self):
        self.refund_cash(10000);self.finance()
        # Refund effective date follows the fixture clock; put it in the reviewed period.
        self.bill.entries.filter(kind='refund').update(date=date(2026,10,21))
        self.finance();self.refresh();data=report('2026-10')
        self.assertEqual(data['totals']['net_before_payouts'],-320)
        self.assertEqual(data['totals']['outstanding'],10000)

    def test_snapshot_is_complete_and_private_fields_are_not_persisted(self):
        self.gateway.transactions['txn_Original'].update(description='PRIVATE',metadata={'secret':'PRIVATE'},destination='PRIVATE')
        snapshot=self.refresh();self.assertNotIn('PRIVATE',str(snapshot.rows))
        self.assertEqual(len(snapshot.rows),1);self.assertEqual(snapshot.checksum,digest(snapshot.rows))

    def test_missing_snapshot_is_unknown_not_zero_confirmed(self):
        response=self.client.get(self.url,{'month':'2026-10'})
        self.assertContains(response,'Unknown');self.assertContains(response,'Read the complete Stripe activity')
        with self.assertRaises(ValidationError):self.review()

    def test_bad_money_and_duplicate_history_never_replace_old_evidence(self):
        old=self.refresh()
        with patch.object(self.gateway,'month_activity',return_value=[self.gateway.transactions['txn_Original']]*2):
            with self.assertRaises(ValidationError):self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),1)
        self.assertEqual(NativeMonthSnapshot.objects.first().pk,old.pk)
        self.assertTrue(report('2026-10')['issues'])
        self.gateway.transactions['txn_Original']['net']=9999
        with self.assertRaises(ValidationError):self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),1)

    def test_non_usd_and_float_money_are_not_accepted(self):
        self.gateway.transactions['txn_Original']['currency']='eur'
        with self.assertRaises(ValidationError):self.refresh()
        self.gateway.transactions['txn_Original']['currency']='usd'
        self.gateway.transactions['txn_Original']['amount']=10000.0
        with self.assertRaises(ValidationError):self.refresh()

    def test_provider_failure_retains_evidence_and_prevents_review(self):
        self.refresh()
        with patch.object(self.gateway,'month_activity',side_effect=GatewayUnavailable('do not leak credentials')):
            with self.assertRaisesMessage(ValidationError,'Previous evidence was retained'):self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),1)
        with self.assertRaises(ValidationError):self.review()

    def test_disappeared_history_blocks_review_and_keeps_both_snapshots(self):
        self.refresh();self.gateway.transactions={};self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),2)
        self.assertTrue(NativeMonthSnapshot.objects.first().history_changed)
        with self.assertRaises(ValidationError):self.review()

    def test_month_account_is_bound_to_confirmed_app_account(self):
        self.refresh()
        with patch.object(self.gateway,'account_id',return_value='acct_Wrong'):
            with self.assertRaises(ValidationError):self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),1)

    def test_refresh_token_replay_does_not_duplicate_evidence(self):
        token=uuid.uuid4();one=self.refresh(token=token);two=self.refresh(token=token)
        self.assertEqual(one.pk,two.pk);self.assertEqual(NativeMonthSnapshot.objects.count(),1)

    def test_latest_refresh_wins_when_reads_overlap(self):
        original=self.gateway.month_activity
        # Simulate a newer generation completing before the first response returns.
        def newer(first,last):
            NativeMonthState.objects.filter(pk=date(2026,10,1)).update(generation=99)
            return original(first,last)
        with patch.object(self.gateway,'month_activity',side_effect=newer):
            with self.assertRaises(EditConflict):self.refresh()
        self.assertEqual(NativeMonthSnapshot.objects.count(),0)

    def test_missing_native_fee_row_in_provider_month_requires_attention(self):
        self.gateway.transactions={};self.refresh()
        self.assertTrue(any('missing' in issue for issue in report('2026-10')['issues']))

    def test_unreconciled_native_fee_history_cannot_disappear_from_review(self):
        self.attempt.finance.state='pending';self.attempt.finance.save()
        self.refresh();self.assertTrue(report('2026-10')['issues'])

    def test_current_month_is_provisional(self):
        self.refresh('2026-11');data=report('2026-11')
        self.assertTrue(any('still in progress' in issue for issue in data['issues']))

    def test_current_month_cannot_be_saved_as_completed(self):
        self.refresh('2026-11')
        with self.assertRaises(ValidationError):self.review(month='2026-11',fingerprint=report('2026-11')['fingerprint'])

    def test_invalid_future_or_unbounded_month_rejected(self):
        for value in ['2026-13','2026-12','1999-01','2026-1','bogus']:
            with self.subTest(value=value),self.assertRaises(ValidationError):month_dates(value)

    def test_eastern_month_boundaries_include_dst_without_fixed_utc_offset(self):
        first,last=bounds(*month_dates('2026-03'))
        self.assertEqual(first.utcoffset(),timedelta(hours=-5));self.assertEqual(last.utcoffset(),timedelta(hours=-4))

    def test_offline_payments_and_credits_are_not_added_to_stripe_receipts(self):
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='payment',amount='25',method='paypal',note='Fictional manual record',date=date(2026,10,22))
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='credit',amount='10',credit_number='TEST-CR-Review',note='Work credit',date=date(2026,10,23))
        self.finance();self.refresh();data=report('2026-10')
        self.assertEqual(data['totals']['offline_payments'],2500);self.assertEqual(data['totals']['credits'],1000)
        self.assertEqual(data['totals']['stripe_payments'],10000);self.assertEqual(data['totals']['credit_balances'],3500)

    def test_later_reversal_does_not_rewrite_prior_month_cutoff(self):
        entry=NativeInvoiceEntry.objects.create(invoice=self.bill,kind='payment',amount=25,method='check',note='Fictional check',date=date(2026,10,22))
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='reversal',amount=25,reverses=entry,note='November correction',date=date(2026,11,2))
        self.finance();self.refresh();data=report('2026-10')
        self.assertEqual(data['totals']['credit_balances'],2500)
        self.assertEqual(data['totals']['offline_corrections'],0)
        self.refresh('2026-11');self.assertEqual(report('2026-11')['totals']['offline_corrections'],-2500)

    def test_later_void_does_not_remove_earlier_invoice_principal(self):
        self.bill.voided_at=datetime(2026,11,2,16,tzinfo=utc.utc);self.bill.save()
        self.finance();self.refresh();data=report('2026-10')
        self.assertEqual(data['totals']['credit_balances'],0)

    def test_saved_review_is_idempotent_and_original_is_not_overwritten(self):
        self.refresh();token=uuid.uuid4();one=self.review(token=token);two=self.review(token=token)
        self.assertEqual(one.pk,two.pk);self.assertEqual(NativeFinancialReview.objects.count(),1)
        original=saved_report(one)
        self.add_transaction('txn_NewAccountFee',None,'stripe_fee',-100,0);self.refresh()
        self.assertEqual(saved_report(one),original)
        self.assertNotEqual(report('2026-10')['fingerprint'],original['fingerprint'])
        response=self.client.get(reverse('workqueue:monthly_finance_detail',args=[one.pk]))
        self.assertContains(response,'Figures changed after this review')

    def test_refresh_with_identical_economics_does_not_invalidate_review(self):
        self.refresh();review=self.review();self.refresh()
        self.assertEqual(report('2026-10')['fingerprint'],review.report['fingerprint'])

    def test_stale_view_or_old_provider_snapshot_cannot_save_review(self):
        self.refresh();stamp=report('2026-10')['fingerprint']
        self.add_transaction('txn_LateFee',None,'stripe_fee',-50,0);self.refresh()
        with self.assertRaises(EditConflict):self.review(fingerprint=stamp)
        NativeMonthState.objects.update(checked_at=timezone.now()-timedelta(hours=2))
        with self.assertRaisesMessage(ValidationError,'within the last hour'):self.review()

    def test_review_requires_owner_note_and_confirmation(self):
        self.refresh()
        for changes in [{'note':''},{'confirm':False}]:
            with self.assertRaises(ValidationError):self.review(**changes)
        staff=get_user_model().objects.create_user('month-staff',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='change_workitem'))
        with self.assertRaises(PermissionDenied):self.review(actor=staff)

    def test_corrupt_saved_report_and_provider_evidence_cannot_be_exported(self):
        source=self.refresh();review=self.review()
        NativeFinancialReview.objects.filter(pk=review.pk).update(checksum='0'*64)
        response=self.client.get(reverse('workqueue:monthly_finance_detail',args=[review.pk]),{'download':'csv'})
        self.assertEqual(response.status_code,409)
        NativeMonthSnapshot.objects.filter(pk=source.pk).update(checksum='0'*64)
        with self.assertRaises(ValidationError):report('2026-10')

    def test_csv_has_exact_signed_amounts_and_neutralizes_customer_formula_text(self):
        self.bill.snapshot['client_name']='=HYPERLINK("malicious")';self.bill.save()
        self.finance();self.refresh()
        NativeInvoiceEntry.objects.create(invoice=self.bill,kind='credit',amount=1,credit_number='TEST-CR-CSV',note='test',date=date(2026,10,22))
        data=report('2026-10');response=export_csv(data);content=response.content.decode('utf-8-sig')
        self.assertIn('100.00,3.20,96.80',content);self.assertIn("'=HYPERLINK",content)
        self.assertIn('TEST-monthly-review-2026-10.csv',response['Content-Disposition'])

    def test_private_get_has_no_network_or_billing_writes(self):
        count=NativeInvoiceEntry.objects.count()
        with patch('workqueue.monthly_finance.TestStripeGateway') as gateway:
            response=self.client.get(self.url,{'month':'2026-10'});gateway.assert_not_called()
        self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(NativeInvoiceEntry.objects.count(),count)
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)

    def test_refresh_is_post_and_csrf_protected(self):
        browser=Client(enforce_csrf_checks=True);browser.force_login(self.actor)
        self.assertEqual(browser.post(self.url,{'month':'2026-10','action':'refresh','token':uuid.uuid4()}).status_code,403)

    def test_monthly_gateway_paginates_and_rejects_overflow_without_write_calls(self):
        gateway=TestStripeGateway.__new__(TestStripeGateway);gateway.client=MagicMock()
        gateway.stripe=MagicMock();gateway.stripe.StripeError=RuntimeError
        first,last=bounds(*month_dates('2026-10'))
        gateway.client.v1.balance_transactions.list.return_value.auto_paging_iter.return_value=iter([{'id':str(i)} for i in range(101)])
        self.assertEqual(len(gateway.month_activity(first,last)),101)
        params=gateway.client.v1.balance_transactions.list.call_args.args[0]
        self.assertEqual(params['created'],{'gte':int(first.timestamp()),'lt':int(last.timestamp())})
        gateway.client.v1.balance_transactions.list.return_value.auto_paging_iter.return_value=iter([{}]*2001)
        with self.assertRaises(GatewayUnavailable):gateway.month_activity(first,last)
        gateway.client.v1.balance_transactions.create.assert_not_called()
