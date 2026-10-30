import copy
import csv
import io
from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, Client as Browser, override_settings
from django.urls import reverse
from .test_freshbooks_mirror import MirrorFixtures, SETTINGS
from .freshbooks_mirror_data import signature
from .freshbooks_mirror_models import FreshBooksMirrorJob, FreshBooksMirrorRecord
from .live_reconciliation_models import FreshBooksPaymentExport, FreshBooksPaymentCheck
from .live_reconciliation import HEADERS, MAX_BYTES, cents, parse_export, compare, import_export, create_check
from .native_invoice_models import NativeInvoiceEntry
from .models import WorkItem, NotificationDelivery


def csv_bytes(*changes):
    output = io.StringIO(newline=''); writer = csv.DictWriter(output, fieldnames=HEADERS); writer.writeheader()
    for change in changes or ({},):
        row = dict(zip(HEADERS, ['Fictional Acme Homes', 'FB-00401', 'Invoice', '2026-09-03 15:09:21+00:00',
            'ch_fictional_1', 'FreshBooks Payments', '1.170', '30.000', 'USD', 'visa', 'succeeded']))
        row.update(change); writer.writerow(row)
    return output.getvalue().encode()


class ExportParserTests(SimpleTestCase):
    def test_five_transaction_control_totals_match_independent_cents(self):
        inputs = [('700.00','20.60'),('367.50','10.96'),('312.50','9.36'),('2500.00','87.80'),('2595.00','91.13')]
        changes=[]; invoices=[]; payments=[]
        for index,(gross,fee) in enumerate(inputs,1):
            changes.append({'Transaction ID':f'ch_fictional_{index}', 'Source Number':f'FICT-{index}', 'Amount':gross,'Fees Paid':fee})
            invoices.append({'provider_id':index,'title':f'FICT-{index}','visibility':0,'currency':'USD','customer_id':101,
                             'amount':gross,'paid':gross,'outstanding':'0'})
            payments.append({'provider_id':index,'provider_invoice_id':index,'visibility':0,'from_credit':False,
                             'currency':'USD','customer_id':101,'date':'2026-09-03','amount':gross,'method':'VISA','gateway':''})
        result=compare(parse_export(csv_bytes(*changes),date(2026,9,1)),invoices,payments)
        total=result['totals'][0]
        self.assertEqual((total['gross'],total['known_fees'],total['net']),(647500,21985,625515))
        self.assertEqual(result['matched'],5)

    def test_real_export_trailing_zero_precision_uses_exact_cents(self):
        row = parse_export(csv_bytes(), date(2026, 9, 1))['rows'][0]
        self.assertEqual((row['gross'], row['fee']), (3000, 117))

    def test_reject_invalid_money_without_rounding(self):
        for value in ['NaN', 'Infinity', '1e3', '1.001', '1,000.00', '$30', ' 30', '', '9'*100]:
            with self.subTest(value=value), self.assertRaises(ValueError): cents(value)

    def test_reject_negative_or_excessive_fee(self):
        for fee in ['-1', '31']:
            with self.assertRaises(ValidationError): parse_export(csv_bytes({'Fees Paid': fee}), date(2026, 9, 1))

    def test_missing_fee_is_unknown_while_zero_is_valid(self):
        for fee in ['', '-']:
            self.assertIsNone(parse_export(csv_bytes({'Fees Paid': fee}), date(2026, 9, 1))['rows'][0]['fee'])
        self.assertEqual(parse_export(csv_bytes({'Fees Paid': '0.000'}), date(2026, 9, 1))['rows'][0]['fee'], 0)

    def test_reject_duplicate_transaction_id(self):
        with self.assertRaises(ValidationError): parse_export(csv_bytes({}, {}), date(2026, 9, 1))

    def test_reject_bad_shape_encoding_size_and_naive_timestamp(self):
        for content in [b'', b'\xff', b'x'*(MAX_BYTES+1), b'Amount,Amount\n1,2', csv_bytes().replace(b'30.000,USD', b'30.000,USD,extra'),
                        csv_bytes({'Transaction Date': '2026-09-03 15:00:00'})]:
            with self.assertRaises(ValidationError): parse_export(content, date(2026, 9, 1))

    def test_eastern_month_boundary_is_explicit(self):
        source = parse_export(csv_bytes({'Transaction Date': '2026-10-01 01:00:00+00:00'}), date(2026, 9, 1))
        self.assertEqual(source['rows'][0]['date'], '2026-09-30')
        self.assertEqual(source['rows'][0]['utc_date'], '2026-10-01')

    def test_outside_period_rows_counted_not_silently_included(self):
        source = parse_export(csv_bytes({}, {'Transaction ID': 'ch_other', 'Transaction Date': '2026-10-02 12:00:00+00:00'}), date(2026, 9, 1))
        self.assertEqual(len(source['rows']), 1); self.assertEqual(source['outside_period'], 1)
        with self.assertRaises(ValidationError): parse_export(csv_bytes(), date(2026, 8, 1))


@override_settings(**SETTINGS)
class LiveReconciliationTests(MirrorFixtures, TestCase):
    def setUp(self):
        self.seed()
        self.remote['payments'][0].update(date='2026-09-03', type='VISA')
        self.sync()
        self.url = reverse('workqueue:live_reconciliation')

    def imported(self, content=None):
        return import_export(actor=self.owner, content=content or csv_bytes(), filename='transactions.csv', month='2026-09')

    def checked(self, content=None):
        return create_check(actor=self.owner, evidence_id=self.imported(content).pk)

    def test_exact_gross_fee_net_and_unique_invoice_payment_match(self):
        result = self.checked().result; total = result['totals'][0]
        self.assertEqual((total['gross'], total['known_fees'], total['net']), (3000, 117, 2883))
        self.assertEqual(result['matched'], 1); self.assertTrue(total['net_complete'])
        self.assertEqual(result['rows'][0]['payment_id'], 301)
        self.assertEqual(result['rows'][0]['invoice_id'], 401)

    def test_source_double_import_and_unchanged_comparison_are_idempotent(self):
        evidence = self.imported(); self.assertEqual(evidence.pk, self.imported().pk)
        first = create_check(actor=self.owner, evidence_id=evidence.pk)
        self.assertEqual(first.pk, create_check(actor=self.owner, evidence_id=evidence.pk).pk)
        self.assertEqual(FreshBooksPaymentExport.objects.count(), 1)

    def test_source_fees_not_estimated_and_zero_is_retained(self):
        self.assertEqual(self.checked(csv_bytes({'Fees Paid': '0.000'})).result['totals'][0]['net'], 3000)
        result = self.checked(csv_bytes({'Fees Paid': ''})).result
        self.assertFalse(result['totals'][0]['net_complete']); self.assertEqual(result['totals'][0]['unknown_fees'], 1)

    def test_declined_attempt_does_not_add_gross_or_fee(self):
        result = self.checked(csv_bytes({}, {'Transaction ID': 'ch_decline', 'Status': 'declined', 'Fees Paid': ''})).result
        self.assertEqual(result['totals'][0]['gross'], 3000); self.assertEqual(result['totals'][0]['excluded'], 1)
        self.assertEqual(result['review'], 0)

    def test_refund_pending_dispute_and_unknown_status_never_infer_net(self):
        for state in ['refunded', 'pending', 'charged_back', 'surprise']:
            result = self.checked(csv_bytes({}, {'Transaction ID': 'ch_'+state, 'Status': state})).result
            self.assertFalse(result['totals'][0]['net_complete']); self.assertEqual(result['review'], 1)

    def test_currencies_never_combined_or_assumed_equal(self):
        result = self.checked(csv_bytes({}, {'Transaction ID': 'ch_cad', 'Currency': 'CAD'})).result
        self.assertEqual(len(result['totals']), 2)
        self.assertEqual([r['gross'] for r in result['totals']], [3000, 3000])
        self.assertFalse(result['totals'][1]['net_complete'])

    def test_duplicate_equivalent_exported_payments_not_matched_twice(self):
        result = self.checked(csv_bytes({}, {'Transaction ID': 'ch_another'})).result
        self.assertEqual(result['matched'], 1); self.assertEqual(result['review'], 1)

    def test_identical_saved_partial_payments_are_ambiguous(self):
        other = copy.deepcopy(self.remote['payments'][0]); other.update(id=302, logid=302)
        self.remote['payments'].append(other); self.sync()
        result = self.checked().result
        self.assertEqual(result['matched'], 0); self.assertEqual(result['possible_missing_online'], 2)

    def test_unmatched_online_payment_prevents_complete_net(self):
        other=copy.deepcopy(self.remote['payments'][0]);other.update(id=302,logid=302,amount={'amount':'42','code':'USD'})
        self.remote['payments'].append(other);self.sync()
        result=self.checked().result
        self.assertEqual(result['matched'],1);self.assertEqual(result['possible_missing_online'],1)
        self.assertFalse(result['totals'][0]['net_complete'])

    def test_offline_other_payments_visible_without_assumed_fees(self):
        other = copy.deepcopy(self.remote['payments'][0]); other.update(id=302, logid=302, type='Check', amount={'amount':'42', 'code':'USD'})
        self.remote['payments'].append(other); self.sync()
        result = self.checked().result
        self.assertEqual(result['totals'][0]['gross'], 3000)
        self.assertEqual(result['other_payments'][0]['gross'], 4200)
        self.assertFalse(result['other_payments'][0]['possible_online'])

    def test_credits_and_deleted_payments_cannot_match(self):
        for changes in [{'from_credit': True}, {'vis_state': 1}]:
            self.remote['payments'][0].update(changes); self.sync()
            self.assertEqual(self.checked().result['matched'], 0)
            self.remote['payments'][0].update(from_credit=False, vis_state=0)

    def test_archived_payment_and_invoice_still_eligible(self):
        self.remote['payments'][0]['vis_state']=2; self.remote['invoices'][0]['vis_state']=2; self.sync()
        self.assertEqual(self.checked().result['matched'], 1)

    def test_invoice_identity_and_balance_conflicts_are_visible(self):
        self.remote['payments'][0]['clientid']=999; self.remote['invoices'][0]['paid']['amount']='31'; self.sync()
        result = self.checked().result
        self.assertEqual(result['matched'], 0); self.assertEqual(len(result['rows'][0]['issues']), 2)

    def test_changed_invoice_number_and_amount_do_not_match(self):
        for changes in [{'Source Number': 'different'}, {'Amount': '31'}]:
            self.assertEqual(self.checked(csv_bytes(changes)).result['matched'], 0)

    def test_non_invoice_gateway_and_date_boundaries_require_review(self):
        for changes in [{'Source Type': 'Checkout Link'}, {'Gateway': 'PayPal'}, {'Transaction Date': '2026-09-04 01:00:00+00:00'}]:
            self.assertFalse(self.checked(csv_bytes(changes)).result['totals'][0]['net_complete'])

    def test_source_corruption_blocks_new_check(self):
        evidence = self.imported(); FreshBooksPaymentExport.objects.filter(pk=evidence.pk).update(source_hash='0'*64)
        with self.assertRaises(ValidationError): create_check(actor=self.owner, evidence_id=evidence.pk)
        self.assertEqual(FreshBooksPaymentCheck.objects.count(), 0)

    def test_failed_working_stale_scans_and_incomplete_coverage_block(self):
        job=FreshBooksMirrorJob.objects.get(kind='payments'); original={k:getattr(job,k) for k in ['state','complete_at','expected_total','seen_ids']}
        for changes in [{'state':'working'}, {'state':'failed'}, {'complete_at':self.now-timedelta(days=2)}, {'expected_total':2}, {'seen_ids':[]}]:
            FreshBooksMirrorJob.objects.filter(pk=job.pk).update(**original, **{})
            FreshBooksMirrorJob.objects.filter(pk=job.pk).update(**changes)
            with self.assertRaises(ValidationError): self.checked()

    def test_wrong_generation_business_or_hash_blocks(self):
        record=self.record('payments'); original={k:getattr(record,k) for k in ['source_hash','business_id','last_seen_generation']}
        import uuid
        for changes in [{'source_hash':'0'*64}, {'business_id':999}, {'last_seen_generation':uuid.uuid4()}]:
            FreshBooksMirrorRecord.objects.filter(pk=record.pk).update(**original)
            FreshBooksMirrorRecord.objects.filter(pk=record.pk).update(**changes)
            with self.assertRaises(ValidationError): self.checked()

    def test_recheck_preserves_prior_facts_and_result(self):
        evidence=self.imported(); first=create_check(actor=self.owner,evidence_id=evidence.pk)
        original=copy.deepcopy(first.result)
        self.remote['payments'][0]['amount']['amount']='31'; self.sync()
        second=create_check(actor=self.owner,evidence_id=evidence.pk)
        self.assertNotEqual(first.pk,second.pk); first.refresh_from_db(); self.assertEqual(first.result,original)
        self.assertEqual(second.result['matched'],0)

    def test_no_provider_calls_ledger_queue_or_mail_side_effects(self):
        self.api_mock.reset_mock(); before=(NativeInvoiceEntry.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count())
        self.checked(); self.api_mock.assert_not_called()
        self.assertEqual(before,(NativeInvoiceEntry.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count()))

    def test_private_owner_routes_and_csrf(self):
        anon=Browser(); self.assertEqual(anon.get(self.url).status_code,302)
        staff=get_user_model().objects.create_user('ordinary-staff',is_staff=True)
        from django.contrib.auth.models import Permission
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        anon.force_login(staff); self.assertEqual(anon.get(self.url).status_code,403)
        response=self.client.get(self.url); self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        csrf=Browser(enforce_csrf_checks=True); csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url,{}).status_code,403)
        with self.assertRaises(PermissionDenied): import_export(actor=staff,content=csv_bytes(),filename='test.csv',month='2026-09')

    def test_upload_preview_confirm_compare_and_safe_text(self):
        response=self.client.post(self.url,{'month':'2026-09','confirm':'on','file':SimpleUploadedFile('source.csv',csv_bytes({'Source':'<script>alert(1)</script>'}))})
        self.assertEqual(response.status_code,302)
        detail=response['Location']; self.assertContains(self.client.get(detail),'&lt;script&gt;')
        response=self.client.post(detail,{'action':'compare'}); self.assertEqual(response.status_code,302)
        result=self.client.get(response['Location']); self.assertContains(result,'28.83'); self.assertContains(result,'not confirmed here')
        self.assertContains(result,'saved comparison',html=False)

    def test_import_requires_attestation_and_valid_month(self):
        response=self.client.post(self.url,{'month':'2026-09','file':SimpleUploadedFile('source.csv',csv_bytes())})
        self.assertEqual(response.status_code,400); self.assertEqual(FreshBooksPaymentExport.objects.count(),0)
        for month in ['2026-13','2026-09-01','26-09']:
            with self.assertRaises(ValidationError): import_export(actor=self.owner,content=csv_bytes(),filename='source.csv',month=month)

    def test_cross_account_evidence_not_exposed_or_rechecked(self):
        evidence=self.imported(); FreshBooksPaymentExport.objects.filter(pk=evidence.pk).update(account_id='OTHER')
        url=reverse('workqueue:live_reconciliation_detail',args=[evidence.pk])
        self.assertEqual(self.client.get(url).status_code,404)
        with self.assertRaises(ValidationError): create_check(actor=self.owner,evidence_id=evidence.pk)

    def test_corrupt_result_does_not_render_financial_totals(self):
        check=self.checked(); FreshBooksPaymentCheck.objects.filter(pk=check.pk).update(result_hash='0'*64)
        response=self.client.get(reverse('workqueue:live_reconciliation_detail',args=[check.evidence_id]))
        self.assertEqual(response.status_code,409); self.assertNotContains(response,'28.83',status_code=409)
