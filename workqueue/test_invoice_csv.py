import csv
import io
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Client, ClientContact, WorkItem, WorkProject, NotificationDelivery, ClientEvent
from .invoice_csv import HEADERS, parse_export, preview_plan, digest, apply_export, change_batch_visibility
from .invoice_history_models import HistoricalInvoice, InvoiceCSVImport, InvoiceHistoryEvent
from .freshbooks_models import FreshBooksConnection, FreshBooksInvoice
from .pm_models import Proposal, ProposalRevision
from .services import create_work, EditConflict
from .tests import work_data


def export_row(number='0001', **changes):
    values={'Client Name':'Morgan','Invoice #':number,'Date Issued':'2026-10-01','Date Due':'2026-10-20',
        'Invoice Status':'paid','Date Paid':'2026-10-02','Item Name':'Design hours','Item Description':'Roof plan,\naddition',
        'Rate':'75.00','Quantity':'2.00000000','Discount Percentage':'0.00','Line Subtotal':'150.00',
        'Tax 1 Type':'','Tax 1 Amount':'0.00','Tax 2 Type':'','Tax 2 Amount':'0.00','Line Total':'150.00','Currency':'USD'}
    return {**values,**changes}


def uploaded(rows, headers=HEADERS):
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=headers)
    writer.writeheader();writer.writerows(rows)
    return SimpleUploadedFile('freshbooks.csv',('\ufeff'+buffer.getvalue()).encode(),content_type='text/csv')


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class InvoiceCSVTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('invoice-import-owner','owner@example.invalid','test')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.connection=FreshBooksConnection.objects.create(pk=1,account_id='TEST123',business_name='Test business')
        self.client.force_login(self.owner)
        self.url=reverse('workqueue:invoice_import')

    def batch(self, rows=None):
        invoices,source_hash=parse_export(uploaded(rows or [export_row()]))
        return InvoiceCSVImport.objects.create(actor=self.owner,account_id='TEST123',business_name='Test business',
            filename='freshbooks.csv',source_hash=source_hash,invoices=invoices)

    def apply(self,batch,choices=None):
        plan=preview_plan(batch)
        return apply_export(batch.pk,digest(plan),choices or {},self.owner)

    def test_parser_groups_multiline_preserves_leading_zeroes_and_exact_source_fields(self):
        one=export_row();two=export_row(**{'Item Name':'Revision','Line Total':'-25.00','Line Subtotal':'-25.00','Quantity':'-0.33333333'})
        invoices,_=parse_export(uploaded([one,two]))
        self.assertEqual(len(invoices),1);self.assertEqual(invoices[0]['number'],'0001')
        self.assertEqual(invoices[0]['total'],'125.00');self.assertEqual(invoices[0]['lines'],[one,two])
        reversed_lines,_=parse_export(uploaded([two,one]))
        self.assertEqual(invoices[0]['fingerprint'],reversed_lines[0]['fingerprint'])

    def test_identical_lines_are_preserved_not_deduplicated(self):
        invoices,_=parse_export(uploaded([export_row(),export_row()]))
        self.assertEqual(invoices[0]['total'],'300.00');self.assertEqual(len(invoices[0]['lines']),2)

    def test_reordered_headers_do_not_change_values_or_detail_column_order(self):
        invoice=self.batch()
        reordered,_=parse_export(uploaded([export_row()],tuple(reversed(HEADERS))))
        invoice.invoices=reordered;invoice.save();self.apply(invoice)
        record=HistoricalInvoice.objects.get()
        response=self.client.get(reverse('workqueue:invoice_history_detail',args=[record.pk]))
        self.assertEqual(response.context['line_cells'][0][:4],['Design hours','Roof plan,\naddition','75.00','2.00000000'])

    def test_invalid_dates_money_headers_encoding_limits_and_metadata_conflicts_fail(self):
        for changes in [{'Date Issued':''},{'Date Due':'yesterday'},{'Rate':'NaN'},{'Line Total':'Infinity'},
            {'Line Total':'1.001'},{'Quantity':'1e1000'},{'Client Name':''},{'Invoice #':''},{'Currency':'usd'},
            {'Item Description':'x'*10001},{'Item Description':'bad\x00value'}]:
            with self.assertRaises(ValidationError,msg=str(changes)):parse_export(uploaded([export_row(**changes)]))
        with self.assertRaises(ValidationError):parse_export(SimpleUploadedFile('bad.csv',b'\xff'))
        with self.assertRaises(ValidationError):parse_export(SimpleUploadedFile('big.csv',b'x'*(2*1024*1024+1)))
        with self.assertRaises(ValidationError):parse_export(SimpleUploadedFile('bad.csv',b'Client Name,Client Name\nA,B\n'))
        with self.assertRaises(ValidationError):parse_export(uploaded([]))
        with self.assertRaises(ValidationError):parse_export(uploaded([export_row(),export_row(**{'Invoice Status':'sent'})]))

    def test_client_names_exact_unique_only_and_contact_suggestions_require_selection(self):
        target=self.item.client_contact.client
        plan=preview_plan(self.batch([export_row(**{'Client Name':target.name})]))
        self.assertEqual(plan['mappings'][0]['recommended'],str(target.pk))
        second=Client.objects.create(name=target.name,normalized_name=target.normalized_name)
        plan=preview_plan(self.batch([export_row(**{'Client Name':target.name})]))
        self.assertEqual(plan['mappings'][0]['recommended'],'');self.assertEqual(len(plan['mappings'][0]['candidates']),2)
        suggestion=Client.objects.create(name='Office',normalized_name='office')
        ClientContact.objects.create(client=suggestion,full_name='CSV Person')
        plan=preview_plan(self.batch([export_row(**{'Client Name':'CSV Person'})]))
        self.assertEqual(plan['mappings'][0]['recommended'],'');self.assertEqual(plan['mappings'][0]['candidates'][0]['id'],str(suggestion.pk))

    def test_import_assigns_reviewed_clients_without_creating_jobs_contacts_or_proposals(self):
        target=self.item.client_contact.client
        batch=self.batch([export_row(**{'Client Name':target.name})])
        before=(Client.objects.count(),ClientContact.objects.count(),WorkItem.objects.count(),Proposal.objects.count(),NotificationDelivery.objects.count())
        with patch('workqueue.freshbooks.api') as api:
            result=self.apply(batch,{'0':str(target.pk)})
        api.assert_not_called()
        self.assertEqual(result['imported'],1);self.assertEqual(result['matched'],1)
        record=HistoricalInvoice.objects.get();self.assertEqual(record.client_id,target.pk);self.assertIsNone(record.project)
        self.assertEqual(record.line_total,Decimal('150'));self.assertEqual(record.exported_status,'paid')
        self.assertEqual(before,(Client.objects.count(),ClientContact.objects.count(),WorkItem.objects.count(),Proposal.objects.count(),NotificationDelivery.objects.count()))
        self.assertEqual(ClientEvent.objects.filter(action='Invoice history imported').count(),1)

    def test_unmatched_names_stay_unassigned_and_forged_match_is_rejected_atomically(self):
        batch=self.batch([export_row(**{'Client Name':'Missing company'})])
        with self.assertRaises(ValidationError):self.apply(batch,{'0':str(self.item.client_contact.client_id)})
        self.assertFalse(HistoricalInvoice.objects.exists());batch.refresh_from_db();self.assertIsNone(batch.applied_at)
        self.assertEqual(self.apply(batch)['unassigned'],1)

    def test_exact_live_invoice_number_in_same_account_is_skipped_and_other_account_does_not_collide(self):
        proposal=Proposal.objects.create(project=self.item.project,client=self.item.client_contact.client,number='EST-TEST',title='Test')
        revision=ProposalRevision.objects.create(proposal=proposal,sequence=1,snapshot={},recipient='owner@example.invalid')
        live=FreshBooksInvoice.objects.create(proposal=proposal,revision=revision,account_id='TEST123',customer_id=7,marker='Test',payload={},invoice_number='0001')
        batch=self.batch();self.assertEqual(self.apply(batch)['linked'],1);self.assertFalse(HistoricalInvoice.objects.exists())
        FreshBooksInvoice.objects.filter(pk=live.pk).update(account_id='OTHER')
        self.assertEqual(self.apply(self.batch())['imported'],1)

    def test_repeat_import_is_idempotent_and_changed_export_does_not_overwrite(self):
        batch=self.batch();self.apply(batch);record=HistoricalInvoice.objects.get()
        self.assertEqual(self.apply(batch)['imported'],1) # Original result, no second write.
        again=self.batch();self.assertEqual(self.apply(again)['duplicate'],1)
        changed=self.batch([export_row(**{'Line Total':'250.00','Line Subtotal':'250.00'})])
        self.assertEqual(self.apply(changed)['conflict'],1);record.refresh_from_db();self.assertEqual(record.line_total,150)
        self.assertEqual(HistoricalInvoice.objects.count(),1)

    def test_concurrent_invoice_or_client_changes_require_a_refreshed_preview(self):
        batch=self.batch();plan=digest(preview_plan(batch));self.apply(self.batch())
        with self.assertRaises(EditConflict):apply_export(batch.pk,plan,{},self.owner)
        target=self.item.client_contact.client
        batch=self.batch([export_row('0002',**{'Client Name':target.name})]);plan=digest(preview_plan(batch))
        Client.objects.filter(pk=target.pk).update(version=target.version+1)
        with self.assertRaises(EditConflict):apply_export(batch.pk,plan,{'0':str(target.pk)},self.owner)

    def test_business_changes_and_wrong_actor_cannot_apply_batch(self):
        batch=self.batch();FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
        with self.assertRaises(EditConflict):self.apply(batch)
        viewer=get_user_model().objects.create_user('history-viewer',is_staff=True)
        with self.assertRaises(PermissionDenied):apply_export(batch.pk,digest(preview_plan(batch)),{},viewer)

    def test_undo_restore_is_recoverable_and_repeat_import_cannot_revive_hidden_records(self):
        batch=self.batch();self.apply(batch)
        change_batch_visibility(batch.pk,self.owner,True)
        record=HistoricalInvoice.objects.get();self.assertIsNotNone(record.removed_at)
        self.assertEqual(self.apply(self.batch())['removed'],1)
        change_batch_visibility(batch.pk,self.owner,False);record.refresh_from_db();self.assertIsNone(record.removed_at)
        self.assertEqual(HistoricalInvoice.objects.count(),1)

    def test_web_upload_review_signature_confirmation_and_actor_binding(self):
        response=self.client.post(self.url,{'file':uploaded([export_row()])})
        self.assertEqual(response.status_code,302)
        response=self.client.get(response.url);batch=response.context['batch'];preview_url=response.request['PATH_INFO']
        self.assertEqual(response.context['summary']['import'],1)
        self.assertEqual(self.client.post(preview_url,{'signature':response.context['signature']}).status_code,400)
        self.assertEqual(self.client.post(preview_url,{'signature':'bad','confirmed':'on'}).status_code,400)
        self.assertFalse(HistoricalInvoice.objects.exists())
        result=self.client.post(preview_url,{'signature':response.context['signature'],'confirmed':'on'})
        self.assertEqual(result.status_code,302);self.assertEqual(HistoricalInvoice.objects.count(),1)
        other=get_user_model().objects.create_superuser('other-history-owner','other@example.invalid','test')
        self.client.force_login(other);self.assertEqual(self.client.get(preview_url).status_code,404)

    def test_history_search_filters_counts_money_and_pagination(self):
        batch=self.batch([export_row(f'{i:04d}',**{'Invoice Status':'paid' if i else 'overdue'}) for i in range(31)])
        self.apply(batch)
        url=reverse('workqueue:invoice_history')
        response=self.client.get(url);self.assertEqual(response.context['summary']['total'],31)
        self.assertEqual(response.context['totals'][0]['exported_total'],4650);self.assertEqual(len(response.context['page']),30)
        self.assertEqual(len(self.client.get(url,{'page':2}).context['page']),1)
        self.assertEqual(self.client.get(url,{'state':'paid'}).context['summary']['total'],30)
        self.assertEqual(self.client.get(url,{'state':'unpaid'}).context['summary']['total'],1)
        self.assertEqual(self.client.get(url,{'q':'Roof plan'}).context['summary']['total'],31)
        self.assertEqual(self.client.get(url,{'q':'0005'}).context['summary']['total'],1)
        self.assertEqual(self.client.get(url,{'client':'not-uuid'}).status_code,404)
        self.assertContains(response,'not current invoice balances')

    def test_associations_do_not_change_exported_facts_and_stale_saves_are_rejected(self):
        self.apply(self.batch());record=HistoricalInvoice.objects.get()
        url=reverse('workqueue:invoice_history_detail',args=[record.pk])
        target=self.item.client_contact.client
        data={'client':target.pk,'project':self.item.project_id,'version':1}
        self.assertEqual(self.client.post(url,data).status_code,302)
        record.refresh_from_db();self.assertEqual(record.client_id,target.pk);self.assertEqual(record.project_id,self.item.project_id)
        self.assertEqual(record.line_total,150);self.assertEqual(record.exported_client,'Morgan')
        self.assertEqual(InvoiceHistoryEvent.objects.count(),1)
        self.assertEqual(self.client.post(url,data).status_code,400)
        history_url=reverse('workqueue:invoice_history')
        self.assertEqual(self.client.get(history_url,{'project':self.item.project_id}).context['summary']['total'],1)
        self.assertEqual(self.client.get(history_url,{'client':target.pk}).context['summary']['total'],1)

    def test_removed_detail_post_is_safe_and_readonly_users_have_no_edit_controls(self):
        batch=self.batch();self.apply(batch);record=HistoricalInvoice.objects.get()
        url=reverse('workqueue:invoice_history_detail',args=[record.pk])
        change_batch_visibility(batch.pk,self.owner,True)
        self.assertEqual(self.client.post(url,{'version':1}).status_code,400)
        viewer=get_user_model().objects.create_user('invoice-csv-reader',is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(viewer);self.assertEqual(self.client.get(reverse('workqueue:invoice_history')).status_code,200)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertNotContains(self.client.get(url),'Save associations')
        self.assertEqual(self.client.post(url,{'version':1}).status_code,403)

    def test_private_cache_feature_gate_escaping_and_no_side_effects_on_reads(self):
        self.apply(self.batch([export_row(**{'Item Description':'<script>bad()</script>','Client Name':'=HYPERLINK("bad")'})]))
        record=HistoricalInvoice.objects.get();url=reverse('workqueue:invoice_history_detail',args=[record.pk])
        before=(WorkItem.objects.count(),NotificationDelivery.objects.count(),InvoiceHistoryEvent.objects.count())
        with patch('workqueue.freshbooks.api') as api:
            response=self.client.get(url)
        api.assert_not_called();self.assertContains(response,'&lt;script&gt;');self.assertNotContains(response,'<script>bad()')
        self.assertIn('no-store',response['Cache-Control']);self.assertEqual(before,(WorkItem.objects.count(),NotificationDelivery.objects.count(),InvoiceHistoryEvent.objects.count()))
        with override_settings(WORK_QUEUE_ENABLED=False):self.assertEqual(self.client.get(url).status_code,404)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)
