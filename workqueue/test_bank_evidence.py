import copy
import csv
import io
import time
from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, Client as Browser, override_settings
from django.urls import reverse
from .test_freshbooks_mirror import MirrorFixtures, SETTINGS
from .freshbooks_models import FreshBooksConnection
from .freshbooks_mirror_data import signature
from .bank_evidence_models import PayoutEvidenceImport, LivePayoutEvidence, BankStatementImport, BankStatementEntry, LiveDepositMatch
from .bank_evidence import (PAYOUT_HEADERS, MAX_BYTES, csv_rows, parse_payouts, import_payouts, bank_upload,
    unpack, map_bank, save_statement, bank_cents, confirm_match, undo_match, match_state, entry_intact, void_statement)
from .services import EditConflict
from .native_invoice_models import NativeInvoiceEntry
from .models import WorkItem, NotificationDelivery


def csv_content(headers,rows):
    output=io.StringIO(newline='');writer=csv.writer(output);writer.writerow(headers);writer.writerows(rows);return output.getvalue().encode('utf-8')


def payout_csv(*changes):
    records=[]
    for change in changes or ({},):
        raw={key:'' for key in PAYOUT_HEADERS}
        raw.update({'id':'po_fictional1','Amount':'1339.08','Created (UTC)':'2026-09-08 00:22','Currency':'usd','Livemode':'true',
            'Arrival Date (UTC)':'2026-09-08 00:00','Source Type':'card','Destination':'ba_fictional',
            'Status':'paid','Type':'bank_account','Method':'standard','Description':'STRIPE PAYOUT',
            'Balance Transaction':'txn_fictional','Trace ID':'000111222333444','Trace ID Status':'supported',
            'Destination Name':'FICTIONAL BANK','Destination Country':'US','Destination Last 4':'0001'})
        raw.update(change);records.append([raw[h] for h in PAYOUT_HEADERS])
    return csv_content(PAYOUT_HEADERS,records)


def bank_csv(rows=None):
    return csv_content(['Date','Description','Amount','Reference','Bank ID'],rows or [
        ['09/08/2026','Fictional processor deposit','1,339.08','000111222333444','BANK-1'],
        ['09/09/2026','Fictional withdrawal','(20.00)','','BANK-2'],
        ['09/10/2026','Zero','0.00','','BANK-3']])


MAPPING={'date_format':'us','mode':'signed','date_column':0,'description_column':1,'amount_column':2,
         'reference_column':3,'id_column':4,'credit_column':None,'debit_column':None,'currency_column':None}


class BankMoneyTests(SimpleTestCase):
    def test_exact_cents_thousands_and_parenthesized_withdrawals(self):
        self.assertEqual(bank_cents('$1,339.080'),133908)
        self.assertEqual(bank_cents('(20.00)'),-2000)
        self.assertEqual(bank_cents('0.00'),0);self.assertIsNone(bank_cents(''))
        for value in ['NaN','1e4','12,34.56','1.001','1.234,56','(-20.00)','--1','9'*100,'$']:
            with self.subTest(value=value),self.assertRaises(ValueError):bank_cents(value)

    def test_raw_csv_rejects_nul_bad_encoding_and_oversize(self):
        for data in [b'',b'\xff',b'X'*(MAX_BYTES+1),b'Date,Description\n2026-09-01,\x00']:
            with self.assertRaises(ValidationError):csv_rows(data)


@override_settings(**SETTINGS)
class BankEvidenceTests(MirrorFixtures,TestCase):
    def setUp(self):
        self.seed();self.url=reverse('workqueue:bank_reconciliation')
        self.batch=import_payouts(actor=self.owner,content=payout_csv(),filename='fictional-payouts.csv',month='2026-09')
        self.payout=LivePayoutEvidence.objects.get()

    def upload(self,content=None,**kwargs):
        values={'actor':self.owner,'content':content or bank_csv(),'filename':'fictional-bank.csv','month':'2026-09',
            'bank_label':'FICTIONAL checking','bank_last4':'0001','currency':'USD','encoding':'utf-8-sig','header_row':1}
        values.update(kwargs);return bank_upload(**values)

    def preview(self,content=None,mapping=None,**kwargs):
        return map_bank(actor=self.owner,payload=self.upload(content,**kwargs),mapping=mapping or MAPPING)

    def statement(self,content=None,mapping=None,**kwargs):
        return save_statement(actor=self.owner,payload=self.preview(content,mapping,**kwargs),confirm=True)

    def match(self,entry=None,**kwargs):
        self.payout.refresh_from_db()
        entry=entry or self.statement().entries.first()
        values={'actor':self.owner,'payout_id':self.payout.pk,'entry_id':entry.pk,'version':self.payout.version,'confirm':True,'note':''}
        values.update(kwargs);return confirm_match(**values)

    def test_actual_export_schema_and_arrival_date_semantics(self):
        data=self.payout.source
        self.assertEqual(data['amount'],133908)
        self.assertEqual(data['initiated_date'],'2026-09-07')
        self.assertEqual(data['arrival_date'],'2026-09-08')
        self.assertEqual(data['last4'],'0001');self.assertEqual(data['trace'],'000111222333444')

    def test_three_payout_control_total(self):
        source=parse_payouts(payout_csv({}, {'id':'po_fictional2','Amount':'2412.20'}, {'id':'po_fictional3','Amount':'2503.87'}),date(2026,9,1))
        self.assertEqual(sum(row['amount'] for row in source['rows']),625515)

    def test_test_payouts_duplicate_ids_headers_and_money_rejected(self):
        cases=[payout_csv({'Livemode':'false'}),payout_csv({},{}),payout_csv({'Amount':'1.001'}),payout_csv({'Destination Last 4':'1'}),payout_csv({'Created (UTC)':'bad'}),b'Amount,Date\n1,2']
        for content in cases:
            with self.assertRaises(ValidationError):parse_payouts(content,date(2026,9,1))

    def test_no_matching_month_does_not_certify_zero(self):
        with self.assertRaises(ValidationError):parse_payouts(payout_csv(),date(2026,8,1))

    def test_padded_export_keeps_eastern_month_end_and_excludes_prior_month(self):
        source=parse_payouts(payout_csv({'Created (UTC)':'2026-10-01 01:00'},
            {'id':'po_prior','Created (UTC)':'2026-09-01 01:00'}),date(2026,9,1))
        self.assertEqual(len(source['rows']),1)
        self.assertEqual(source['rows'][0]['initiated_date'],'2026-09-30')
        self.assertEqual(source['outside_period'],1)

    def test_payout_duplicate_import_does_not_replay_old_head(self):
        self.assertEqual(import_payouts(actor=self.owner,content=payout_csv(),filename='again.csv',month='2026-09').pk,self.batch.pk)
        changed=payout_csv({'Status':'failed'})
        import_payouts(actor=self.owner,content=changed,filename='new.csv',month='2026-09')
        import_payouts(actor=self.owner,content=payout_csv(),filename='old.csv',month='2026-09')
        self.payout.refresh_from_db();self.assertEqual(self.payout.source['status'],'failed')

    def test_preview_does_not_store_bank_data_and_save_retains_only_deposits(self):
        payload=self.preview();self.assertEqual(BankStatementImport.objects.count(),0)
        source=unpack(payload,self.owner,'preview')['source']
        self.assertEqual(len(source['rows']),1);self.assertEqual(source['withdrawals_ignored'],1);self.assertEqual(source['zeros_ignored'],1)
        bank=save_statement(actor=self.owner,payload=payload,confirm=True)
        self.assertEqual(bank.entries.count(),1);self.assertNotIn('Fictional withdrawal',str(bank.source))
        self.assertNotIn('content',bank.source)

    def test_statement_reimport_and_mapping_replay_are_idempotent(self):
        payload=self.preview();first=save_statement(actor=self.owner,payload=payload,confirm=True)
        second=save_statement(actor=self.owner,payload=payload,confirm=True)
        self.assertEqual(first.pk,second.pk);self.assertEqual(BankStatementEntry.objects.count(),1)

    def test_preview_bound_to_owner_business_stage_and_expiry(self):
        payload=self.upload()
        with self.assertRaises(ValidationError):unpack(payload+'changed',self.owner,'mapping')
        with self.assertRaises(ValidationError):unpack(payload,self.owner,'preview')
        other=get_user_model().objects.create_superuser('other-owner','other@example.invalid','fictional')
        with self.assertRaises(ValidationError):unpack(payload,other,'mapping')
        FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
        with self.assertRaises(ValidationError):unpack(payload,self.owner,'mapping')
        FreshBooksConnection.objects.filter(pk=1).update(account_id='TEST123')
        with patch('django.core.signing.time.time',return_value=time.time()+1900),self.assertRaises(ValidationError):unpack(payload,self.owner,'mapping')

    def test_header_row_and_windows_encoding(self):
        content=b'Account summary\nDate,Description,Amount,Reference,Bank ID\n09/08/2026,Deposit\x96bank,1339.08,000111222333444,BANK-1\n'
        payload=self.upload(content,header_row=2,encoding='cp1252')
        value=map_bank(actor=self.owner,payload=payload,mapping=MAPPING)
        self.assertEqual(len(unpack(value,self.owner,'preview')['source']['rows']),1)

    def test_signed_and_split_columns_have_same_exact_result(self):
        content=csv_content(['Date','Description','Credit','Debit','Reference','ID'],[
            ['2026-09-08','Deposit','1339.08','','000111222333444','ONE'],
            ['2026-09-09','Withdrawal','','20.00','','TWO']])
        mapping={'date_format':'iso','mode':'split','date_column':0,'description_column':1,'amount_column':None,
            'credit_column':2,'debit_column':3,'reference_column':4,'id_column':5,'currency_column':None}
        bank=self.statement(content,mapping)
        self.assertEqual(bank.entries.get().amount,133908)
        self.assertEqual(bank.source['withdrawals_ignored'],1)
        for values in [['20','10'],['-20','']]:
            bad=csv_content(['Date','Description','Credit','Debit','Reference','ID'],[['2026-09-08','Bad',*values,'','ONE']])
            with self.assertRaises(ValidationError):self.preview(bad,mapping)

    def test_missing_duplicate_columns_invalid_dates_and_subcents_rejected(self):
        for mapping in [dict(MAPPING,date_column=1),dict(MAPPING,amount_column=None),dict(MAPPING,date_format='unknown')]:
            with self.assertRaises(ValidationError):self.preview(mapping=mapping)
        for values in [['13/08/2026','Deposit','1339.08','REF','ID'],['09/08/2026','Deposit','1339.081','REF','ID']]:
            with self.assertRaises(ValidationError):self.preview(bank_csv([values]))

    def test_currency_column_conflict_is_not_silently_converted(self):
        content=csv_content(['Date','Description','Amount','Currency'],[['09/08/2026','Deposit','1339.08','CAD']])
        mapping=dict(MAPPING,id_column=None,reference_column=None,currency_column=3)
        with self.assertRaises(ValidationError):self.preview(content,mapping)

    def test_empty_and_future_deposits_do_not_confirm_zero_or_pending_funds(self):
        for values in [['09/08/2026','Withdrawal','-1','','ID'],['10/29/2026','Future deposit','1339.08','REF','ID']]:
            with self.assertRaises(ValidationError):self.preview(bank_csv([values]))

    def test_bank_match_uses_payout_amount_without_subtracting_fee_again(self):
        record=self.match();self.assertEqual(match_state(record),'confirmed')
        self.assertEqual(record.evidence['payout']['amount'],133908)
        self.assertEqual(record.evidence['bank_entry']['amount'],133908)
        self.assertTrue(record.evidence['exact_reference'])

    def test_equal_amount_without_reference_requires_review_explanation(self):
        entry=self.statement(bank_csv([['09/08/2026','Unidentified deposit','1339.08','','ID']])).entries.get()
        with self.assertRaises(ValidationError):self.match(entry)
        record=self.match(entry,note='Checked the original bank statement and payout detail manually.')
        self.assertEqual(match_state(record),'confirmed');self.assertFalse(record.evidence['exact_reference'])

    def test_owner_attestation_required_even_with_exact_trace(self):
        with self.assertRaises(ValidationError):self.match(confirm=False)
        with self.assertRaises(ValidationError):save_statement(actor=self.owner,payload=self.preview(),confirm=False)

    def test_payout_and_bank_deposit_cannot_be_reused(self):
        entry=self.statement().entries.get();self.match(entry)
        with self.assertRaises(ValidationError):self.match(entry)
        import_payouts(actor=self.owner,content=payout_csv({}, {'id':'po_fictionalother'}),filename='two.csv',month='2026-09')
        other=LivePayoutEvidence.objects.get(provider_id='po_fictionalother')
        with self.assertRaises(ValidationError):confirm_match(actor=self.owner,payout_id=other.pk,entry_id=entry.pk,version=other.version,confirm=True,note='Independently reviewed the transaction.')

    def test_cross_file_same_deposit_not_counted_twice(self):
        first=self.statement().entries.get();self.match(first)
        mapping=dict(MAPPING,date_format='iso')
        second=self.statement(bank_csv([['2026-09-08','Fictional processor deposit','1339.08','000111222333444','BANK-1']]),mapping).entries.get()
        self.assertEqual(first.identity_key,second.identity_key)
        self.assertTrue(entry_intact(second))
        import_payouts(actor=self.owner,content=payout_csv({}, {'id':'po_fictionalother'}),filename='two.csv',month='2026-09')
        other=LivePayoutEvidence.objects.get(provider_id='po_fictionalother')
        with self.assertRaises(ValidationError):confirm_match(actor=self.owner,payout_id=other.pk,entry_id=second.pk,version=other.version,confirm=True,note='Checked independently.')

    def test_duplicate_rows_without_ids_cannot_confirm(self):
        content=bank_csv([['09/08/2026','Deposit','1339.08','000111222333444',''],['09/08/2026','Deposit','1339.08','000111222333444','']])
        bank=self.statement(content);self.assertEqual(bank.entries.count(),2)
        with self.assertRaises(ValidationError):self.match(bank.entries.first())

    def test_same_date_and_amount_without_distinct_ids_requires_more_evidence(self):
        first=self.statement(bank_csv([['09/08/2026','First deposit','1339.08','TRACE-A','']])).entries.get()
        second=self.statement(bank_csv([['09/08/2026','Different description','1339.08','TRACE-B','']])).entries.get()
        self.match(first,note='Reviewed the first bank deposit manually.')
        import_payouts(actor=self.owner,content=payout_csv({}, {'id':'po_fictionalother'}),filename='two.csv',month='2026-09')
        other=LivePayoutEvidence.objects.get(provider_id='po_fictionalother')
        with self.assertRaises(ValidationError):confirm_match(actor=self.owner,payout_id=other.pk,entry_id=second.pk,version=other.version,confirm=True,note='Reviewed the second bank deposit manually.')

    def test_distinct_bank_ids_can_prove_two_equal_deposits(self):
        content=bank_csv([['09/08/2026','Deposit','1339.08','000111222333444','BANK-1'],['09/08/2026','Deposit','1339.08','000111222333444','BANK-2']])
        bank=self.statement(content);first,second=bank.entries.all();self.match(first)
        import_payouts(actor=self.owner,content=payout_csv({}, {'id':'po_fictionalother'}),filename='two.csv',month='2026-09')
        other=LivePayoutEvidence.objects.get(provider_id='po_fictionalother')
        record=confirm_match(actor=self.owner,payout_id=other.pk,entry_id=second.pk,version=other.version,confirm=True,note='')
        self.assertEqual(match_state(record),'confirmed');self.assertEqual(LiveDepositMatch.objects.count(),2)

    def test_conflicting_bank_id_invalidates_old_match_and_can_be_corrected(self):
        record=self.match()
        bank=self.statement(bank_csv([['09/08/2026','Fictional processor deposit','1340.08','000111222333444','BANK-1']]))
        self.assertEqual(match_state(record),'review')
        void_statement(actor=self.owner,statement_id=bank.pk,reason='Incorrect amount mapping; original statement verified.')
        self.assertEqual(match_state(record),'confirmed')
        self.assertEqual(BankStatementImport.objects.count(),2)

    def test_updated_failed_or_missing_payout_invalidates_prior_match(self):
        record=self.match()
        import_payouts(actor=self.owner,content=payout_csv({'Status':'failed'}),filename='failed.csv',month='2026-09')
        record.payout.refresh_from_db();self.assertEqual(match_state(record),'review')
        import_payouts(actor=self.owner,content=payout_csv({'id':'po_replacement'}),filename='replacement.csv',month='2026-09')
        record.payout.refresh_from_db();self.assertTrue(record.payout.missing);self.assertEqual(match_state(record),'review')

    def test_undo_preserves_evidence_and_allows_corrected_review(self):
        record=self.match();old=copy.deepcopy(record.evidence)
        undo_match(actor=self.owner,match_id=record.pk,reason='Review correction for the fictional statement.')
        record.refresh_from_db();self.assertEqual(match_state(record),'corrected');self.assertEqual(record.evidence,old)
        self.match(record.bank_entry);self.assertEqual(LiveDepositMatch.objects.count(),2)

    def test_void_statement_invalidates_confirmation_and_excludes_new_matches(self):
        record=self.match();void_statement(actor=self.owner,statement_id=record.bank_entry.statement_id,reason='Wrong account export.')
        record.bank_entry.statement.refresh_from_db();self.assertEqual(match_state(record),'review')
        with self.assertRaises(ValidationError):self.match(record.bank_entry)

    def test_mismatched_amount_currency_account_date_and_pending_status_rejected(self):
        cases=[{'currency':'CAD'},{'bank_last4':'9999'}]
        for change in cases:
            bank=self.statement(**change)
            with self.assertRaises(ValidationError):self.match(bank.entries.get())
        for values in [['09/08/2026','Deposit','1338.08','000111222333444','DIFF'],['08/01/2026','Deposit','1339.08','000111222333444','OLD']]:
            with self.assertRaises(ValidationError):self.match(self.statement(bank_csv([values])).entries.get())
        import_payouts(actor=self.owner,content=payout_csv({'Status':'pending'}),filename='pending.csv',month='2026-09')
        with self.assertRaises(ValidationError):self.match()

    def test_stale_version_and_source_corruption_rejected(self):
        entry=self.statement().entries.get()
        with self.assertRaises(EditConflict):self.match(entry,version=999)
        BankStatementEntry.objects.filter(pk=entry.pk).update(amount=1)
        with self.assertRaises(ValidationError):self.match(entry)

    def test_no_provider_billing_queue_or_mail_writes(self):
        self.api_mock.reset_mock();before=(NativeInvoiceEntry.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count())
        self.match();self.api_mock.assert_not_called()
        self.assertEqual(before,(NativeInvoiceEntry.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count()))

    def test_owner_only_private_csrf_and_cross_business_routes(self):
        anon=Browser();self.assertEqual(anon.get(self.url).status_code,302)
        staff=get_user_model().objects.create_user('bank-staff',is_staff=True)
        from django.contrib.auth.models import Permission
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        anon.force_login(staff);self.assertEqual(anon.get(self.url).status_code,403)
        with self.assertRaises(PermissionDenied):self.match(actor=staff)
        response=self.client.get(self.url);self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control'])
        csrf=Browser(enforce_csrf_checks=True);csrf.force_login(self.owner);self.assertEqual(csrf.post(self.url,{}).status_code,403)
        FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
        self.assertEqual(self.client.get(reverse('workqueue:bank_payout_detail',args=[self.payout.pk])).status_code,404)

    def test_full_browser_form_flow_and_safe_source_text(self):
        content=bank_csv([['09/08/2026','<script>alert(1)</script>','1339.08','000111222333444','BANK-1']])
        response=self.client.post(self.url,{'action':'bank','month':'2026-09','bank_label':'Fictional bank','bank_last4':'0001','currency':'USD','encoding':'utf-8-sig','header_row':'1','file':SimpleUploadedFile('bank.csv',content)})
        self.assertEqual(response.status_code,200);self.assertContains(response,'&lt;script&gt;')
        payload=response.context['form'].initial['payload']
        mapped=self.client.post(reverse('workqueue:bank_statement_mapping'),{'payload':payload,**{k:'' if v is None else v for k,v in MAPPING.items()}})
        self.assertEqual(mapped.status_code,200)
        saved=self.client.post(reverse('workqueue:bank_statement_save'),{'payload':mapped.context['payload'],'confirm':'on'})
        self.assertEqual(saved.status_code,302)
        entry=BankStatementEntry.objects.get();url=reverse('workqueue:bank_payout_detail',args=[self.payout.pk])
        confirmed=self.client.post(url,{'action':'match','version':self.payout.version,'entry':str(entry.pk),'confirm':'on','note':''})
        self.assertEqual(confirmed.status_code,302)
        page=self.client.get(url);self.assertContains(page,'Owner-reviewed bank CSV match');self.assertContains(page,'&lt;script&gt;')

    def test_bad_month_preview_signature_and_void_without_reason_show_errors(self):
        self.assertEqual(self.client.get(self.url+'?month=invalid').status_code,400)
        self.assertEqual(self.client.post(reverse('workqueue:bank_statement_mapping'),{'payload':'tampered'}).status_code,400)
        self.assertEqual(self.client.post(reverse('workqueue:bank_statement_save'),{'payload':'tampered','confirm':'on'}).status_code,400)
        bank=self.statement()
        response=self.client.post(self.url,{'action':'void','statement':str(bank.pk),'reason':''})
        self.assertEqual(response.status_code,400)

    def test_withdrawals_and_card_destinations_require_separate_review(self):
        for changes in [{'Amount':'-1339.08'},{'Type':'card'}]:
            import_payouts(actor=self.owner,content=payout_csv(changes),filename='unsupported.csv',month='2026-09')
            page=self.client.get(self.url)
            self.assertEqual(page.context['totals'][0]['review'],1)
            detail=self.client.get(reverse('workqueue:bank_payout_detail',args=[self.payout.pk]))
            self.assertNotContains(detail,'Record reviewed bank CSV match')
