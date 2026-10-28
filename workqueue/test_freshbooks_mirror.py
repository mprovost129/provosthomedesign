import copy,uuid
from datetime import datetime,timedelta,timezone as utc
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from .test_freshbooks_projects import SETTINGS
from . import freshbooks as fb
from .freshbooks_models import FreshBooksConnection,FreshBooksClientLink,FreshBooksInvoice
from .freshbooks_mirror_models import FreshBooksMirrorSettings,FreshBooksMirrorJob,FreshBooksMirrorRecord,FreshBooksMirrorRevision
from .freshbooks_mirror import save_settings,request_sync,process_step,process_mirror,link_client
from .freshbooks_mirror_data import normalize,signature
from .models import Client,ClientContact,ClientEvent,WorkItem,WorkProject,NotificationDelivery,QueueState
from .native_invoice_models import NativeInvoiceEntry
from .services import EditConflict
from .crm_identity import normalized_text


def source_client(identifier=101,**changes):
    row={'id':identifier,'userid':identifier,'accounting_systemid':'TEST123','organization':'Acme Homes',
        'fname':'Mike','lname':'Builder','email':'mike@example.invalid','bus_phone':'508-555-0100',
        'p_street':'12 Example Road','p_street2':'','p_city':'Rehoboth','p_province':'MA','p_code':'02769','p_country':'United States',
        'vis_state':0,'updated':'2026-10-28 12:00:00'}
    row.update(changes);return row


def cash(value):return {'amount':str(value),'code':'USD'}


def source_invoice(identifier=401,**changes):
    row={'id':identifier,'invoiceid':identifier,'accountid':'TEST123','accounting_systemid':'TEST123',
        'customerid':101,'invoice_number':'FB-00401','currency_code':'USD','v3_status':'partial','amount':cash('100.00'),
        'paid':cash('30.00'),'outstanding':cash('70.00'),'create_date':'2026-10-20','due_date':'2026-11-20',
        'organization':'Acme Homes','notes':'Fictional invoice notes','terms':'Fictional terms','vis_state':0,
        'discount_total':cash('0.00'),'discount_value':'0','updated':'2026-10-28 12:00:00',
        'lines':[{'name':'Design','description':'Fictional project work','qty':'100','unit_cost':cash('1.00'),
            'amount':cash('100.00'),'taxName1':None,'taxAmount1':None}]}
    row.update(changes);return row


def source_project():
    return {'id':201,'client_id':101,'title':'Fictional Birch Lane project','description':'Fictional plans',
        'active':True,'complete':False,'project_type':'fixed_price','fixed_price':'100','rate':None,
        'due_date':'2026-11-20','updated_at':'2026-10-28T12:00:00','group':{'members':[{'secret':'not copied'}]}}


def source_payment(**changes):
    row={'id':301,'logid':301,'clientid':101,'invoiceid':401,'accounting_systemid':'TEST123',
        'amount':cash('30.00'),'date':'2026-10-22','type':'Check','from_credit':False,'creditid':None,
        'vis_state':0,'gateway':None,'note':'PRIVATE PAYMENT REFERENCE','transactionid':'DO-NOT-COPY',
        'updated':'2026-10-28 12:00:00'}
    row.update(changes);return row


def source_estimate():
    row=source_invoice();row.pop('invoiceid');row.pop('v3_status');row.pop('paid');row.pop('outstanding')
    row.update(id=501,estimateid=501,estimate_number='FB-EST-501',display_status='accepted')
    return row


class MirrorFixtures:
    def seed(self):
        self.now=datetime(2026,10,28,12,tzinfo=utc.utc)
        clock=patch('django.utils.timezone.now',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
        self.owner=get_user_model().objects.create_superuser('mirror-owner','owner@example.invalid','test-only')
        QueueState.objects.get_or_create(pk=1)
        self.connection=FreshBooksConnection.objects.create(account_id='TEST123',business_id=77,business_name='Fictional business',
            granted_scopes=(fb.SCOPES+' user:payments:read user:estimates:read').split())
        fb.store_tokens(self.connection,{'access_token':'fictional-token','refresh_token':'fictional-refresh','expires_in':3600})
        self.remote={'clients':[source_client()],'projects':[source_project()],'invoices':[source_invoice()],
            'payments':[source_payment()],'estimates':[source_estimate()]}
        api=patch('workqueue.freshbooks.api',side_effect=self.api);self.api_mock=api.start();self.addCleanup(api.stop)
        self.settings=save_settings(actor=self.owner,version=1,enabled=True,include_estimates=False)
        self.client.force_login(self.owner);self.url=reverse('workqueue:freshbooks_mirror')

    def api(self,method,path,**kwargs):
        if method!='GET':raise AssertionError('Mirror attempted a provider write')
        kind=path.split('/')[-1];rows=self.remote[kind];params=kwargs['params'];page=params['page']
        if kind in ('invoices','estimates'):self.assertEqual(params['include[]'],'lines')
        result={'page':page,'pages':(len(rows)+99)//100,'per_page':100,'total':len(rows),kind:copy.deepcopy(rows[(page-1)*100:page*100])}
        return {'meta':{k:result[k] for k in ('page','pages','per_page','total')},'projects':result[kind]} if kind=='projects' else {'response':{'result':result}}

    def sync(self):
        self.now+=timedelta(minutes=6)
        return process_mirror(limit=20)

    def record(self,kind='invoices',identifier=None):
        query=FreshBooksMirrorRecord.objects.filter(kind=kind)
        return query.get(provider_id=identifier) if identifier else query.get()

    def crm(self):return self.record('clients').client


@override_settings(**SETTINGS)
class FreshBooksMirrorTests(MirrorFixtures,TestCase):
    def setUp(self):self.seed()

    def test_initial_mirror_copies_four_sources_without_a_second_ledger_or_queue(self):
        self.sync()
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),4)
        self.assertEqual(Client.objects.count(),1);self.assertEqual(ClientContact.objects.count(),1)
        self.assertEqual(WorkItem.objects.count(),0);self.assertEqual(WorkProject.objects.count(),0)
        self.assertEqual(NativeInvoiceEntry.objects.count(),0);self.assertEqual(FreshBooksInvoice.objects.count(),0)
        self.assertEqual(NotificationDelivery.objects.count(),0)
        self.assertEqual(self.record().source['outstanding'],'70.00')
        self.assertEqual(self.record('projects').client_id,self.crm().pk)
        self.assertTrue(ClientContact.objects.get().booking_requires_approval)

    def test_repeated_scans_do_not_duplicate_clients_invoices_or_history(self):
        self.sync();before=FreshBooksMirrorRevision.objects.count();events=ClientEvent.objects.count()
        self.sync();self.sync()
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),4);self.assertEqual(Client.objects.count(),1)
        self.assertEqual(FreshBooksMirrorRevision.objects.count(),before);self.assertEqual(ClientEvent.objects.count(),events)

    def test_source_changes_update_managed_fields_and_preserve_private_crm_fields(self):
        self.sync();client=self.crm();client.internal_notes='PRIVATE LOCAL NOTES';client.pricing_category='contractor';client.save()
        contact=ClientContact.objects.get();contact.booking_requires_approval=False;contact.save()
        self.remote['clients'][0].update(bus_phone='508-555-0199',p_street='99 New Road')
        self.sync();client.refresh_from_db();contact.refresh_from_db()
        self.assertEqual(client.billing_street,'99 New Road');self.assertEqual(contact.phone,'508-555-0199')
        self.assertEqual(client.internal_notes,'PRIVATE LOCAL NOTES');self.assertEqual(client.pricing_category,'contractor')
        self.assertFalse(contact.booking_requires_approval)

    def test_local_manual_edits_are_not_silently_overwritten(self):
        self.sync();client=self.crm();client.name='Locally corrected name';client.save()
        contact=ClientContact.objects.get();contact.phone='508-555-7777';contact.save()
        self.remote['clients'][0].update(organization='New billing name',bus_phone='508-555-8888')
        self.sync();client.refresh_from_db();contact.refresh_from_db()
        self.assertEqual(client.name,'Locally corrected name');self.assertEqual(contact.phone,'508-555-7777')
        self.assertTrue(self.record('clients').issues);self.assertEqual(self.record('clients').source['organization'],'New billing name')

    def test_existing_exact_company_and_email_match_is_reused(self):
        client=Client.objects.create(name='Acme Homes',normalized_name='acme homes',kind='company',internal_notes='Keep')
        ClientContact.objects.create(client=client,email='mike@example.invalid',full_name='Mike Builder')
        self.sync();self.assertEqual(self.crm().pk,client.pk);self.assertEqual(Client.objects.count(),1)
        self.assertEqual(self.crm().internal_notes,'Keep')

    def test_two_companies_with_same_owner_email_remain_separate(self):
        for name in ['Acme Homes','Red Tail Homes']:
            client=Client.objects.create(name=name,normalized_name=normalized_text(name),kind='company')
            ClientContact.objects.create(client=client,email='mike@example.invalid')
        self.remote['clients'].append(source_client(102,organization='Red Tail Homes'))
        self.sync();self.assertEqual(Client.objects.count(),2)
        first=self.record('clients',101);second=self.record('clients',102)
        self.assertNotEqual(first.client_id,second.client_id);self.assertEqual(second.client.name,'Red Tail Homes')

    def test_ambiguous_company_name_variation_requires_review_not_duplicate_creation(self):
        client=Client.objects.create(name='Acme Homes LLC',normalized_name='acme homes llc',kind='company')
        ClientContact.objects.create(client=client,email='mike@example.invalid')
        self.sync();self.assertIsNone(self.record('clients').client_id)
        self.assertTrue(self.record('clients').issues);self.assertEqual(Client.objects.count(),1)

    def test_email_change_keeps_same_contact_and_source_identity(self):
        self.sync();contact=ClientContact.objects.get();identifier=contact.pk
        self.remote['clients'][0]['email']='new-address@example.invalid';self.sync()
        contact.refresh_from_db();self.assertEqual(contact.pk,identifier);self.assertEqual(contact.email,'new-address@example.invalid')
        self.assertEqual(ClientContact.objects.count(),1)

    def test_archived_source_retains_existing_crm_and_records(self):
        self.sync();client_id=self.crm().pk
        self.remote['clients'][0]['vis_state']=2;self.sync()
        self.assertEqual(self.record('clients').client_id,client_id);self.assertEqual(ClientContact.objects.count(),1)
        self.assertEqual(self.record('clients').source['visibility'],2)

    def test_archived_local_contact_is_not_restored_or_duplicated(self):
        self.sync();contact=ClientContact.objects.get();contact.archived_at=self.now;contact.email=None;contact.save()
        self.sync();contact.refresh_from_db()
        self.assertIsNotNone(contact.archived_at);self.assertEqual(ClientContact.objects.count(),1)
        self.assertTrue(any('archived' in issue for issue in self.record('clients').issues))

    def test_invalid_source_email_is_not_used_for_access_or_sending(self):
        self.remote['clients'][0]['email']='invalid-address';self.sync()
        self.assertTrue(self.record('clients').source['invalid_email']);self.assertIsNone(ClientContact.objects.get().email)
        self.assertEqual(NotificationDelivery.objects.count(),0)

    def test_full_success_required_before_not_returned_records_are_flagged(self):
        self.sync();invoice=self.record();original=invoice.source
        self.remote['invoices']=[];self.sync();invoice.refresh_from_db()
        self.assertTrue(invoice.missing);self.assertEqual(invoice.source,original)
        self.assertEqual(invoice.source['outstanding'],'70.00')

    def test_pagination_change_fails_without_false_missing_or_fresh_completion(self):
        self.sync();old=self.record('clients');self.now+=timedelta(minutes=6)
        self.remote['clients']=[source_client(i,organization=f'Client {i}',email=f'client{i}@example.invalid') for i in range(1000,1101)]
        request_sync(automatic=True);self.assertTrue(process_step())
        job=FreshBooksMirrorJob.objects.get(kind='clients');self.assertEqual(job.page,2)
        self.remote['clients'].append(source_client(1200,organization='Extra',email='extra@example.invalid'))
        self.assertTrue(process_step());job.refresh_from_db();old.refresh_from_db()
        self.assertEqual(job.state,'failed');self.assertFalse(old.missing)
        self.assertEqual(job.error_reference,'')

    def test_duplicate_source_id_is_rejected_and_old_source_retained(self):
        self.sync();invoice=self.record();self.remote['invoices']*=2;self.sync()
        job=FreshBooksMirrorJob.objects.get(kind='invoices');invoice.refresh_from_db()
        self.assertEqual(job.state,'failed');self.assertFalse(invoice.missing);self.assertEqual(invoice.revision,1)

    def test_mismatched_account_and_ids_are_not_copied(self):
        self.remote['invoices'][0]['accountid']='OTHER';self.sync()
        self.assertFalse(FreshBooksMirrorRecord.objects.filter(kind='invoices').exists())
        job=FreshBooksMirrorJob.objects.get(kind='invoices');self.assertEqual(job.error_reference,'401')
        self.assertEqual(job.state,'failed')

    def test_invalid_money_does_not_replace_verified_values(self):
        self.sync();self.remote['invoices'][0]['amount']=cash('NaN');self.sync()
        self.assertEqual(self.record().source['amount'],'100.00')
        self.assertEqual(FreshBooksMirrorJob.objects.get(kind='invoices').state,'failed')

    def test_line_precision_and_provider_totals_are_preserved_without_repricing(self):
        row=self.remote['invoices'][0];row['lines'][0].update(qty='3',unit_cost=cash('0.333333'),amount=cash('1.00'))
        row.update(amount=cash('1.08'),paid=cash('0'),outstanding=cash('1.08'));self.sync()
        source=self.record().source
        self.assertEqual(source['amount'],'1.08');self.assertEqual(source['lines'][0]['rate'],'0.333333')
        self.assertEqual(source['lines'][0]['amount'],'1.00')

    def test_mixed_currency_is_preserved_without_conversion_or_added_totals(self):
        row=self.remote['invoices'][0];row['currency_code']='EUR'
        for key in ['amount','paid','outstanding','discount_total']:row[key]['code']='EUR'
        for line in row['lines']:
            line['unit_cost']['code']='EUR';line['amount']['code']='EUR'
        self.sync();self.assertEqual(self.record().source['currency'],'EUR')
        response=self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[self.record().pk]))
        self.assertContains(response,'EUR');self.assertContains(response,'Processor fees and net proceeds are unknown')

    def test_credit_application_is_not_a_new_cash_ledger_entry(self):
        self.remote['payments'][0]['from_credit']=True;self.sync()
        self.assertTrue(self.record('payments').source['from_credit']);self.assertEqual(NativeInvoiceEntry.objects.count(),0)
        response=self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[self.record('payments').pk]))
        self.assertContains(response,'not counted as another cash collection')

    def test_private_processor_references_and_project_members_are_not_persisted(self):
        self.sync();combined=str(list(FreshBooksMirrorRecord.objects.values_list('source',flat=True)))
        self.assertNotIn('PRIVATE PAYMENT REFERENCE',combined);self.assertNotIn('DO-NOT-COPY',combined);self.assertNotIn('not copied',combined)

    def test_expired_worker_lease_resumes_without_duplicate_records(self):
        request_sync(actor=self.owner);job=FreshBooksMirrorJob.objects.get(kind='clients')
        job.state='working';job.lease=uuid.uuid4();job.lease_until=self.now+timedelta(minutes=3);job.save()
        process_step();self.assertFalse(FreshBooksMirrorRecord.objects.filter(kind='clients').exists())
        self.now+=timedelta(minutes=4);process_step()
        self.assertEqual(FreshBooksMirrorRecord.objects.filter(kind='clients').count(),1)

    def test_disabling_mirror_stops_provider_calls(self):
        save_settings(actor=self.owner,version=self.settings.version,enabled=False,include_estimates=False)
        before=self.api_mock.call_count;self.assertEqual(process_mirror(),0);self.assertEqual(before,self.api_mock.call_count)

    def test_pause_during_read_prevents_inflight_apply(self):
        original=self.api
        def pause(method,path,**kwargs):
            FreshBooksMirrorSettings.objects.filter(pk='TEST123').update(enabled=False)
            return original(method,path,**kwargs)
        self.api_mock.side_effect=pause;request_sync(actor=self.owner);process_step()
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),0)

    def test_account_change_during_read_cannot_apply_to_new_business(self):
        original=self.api
        def switch(method,path,**kwargs):
            FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
            return original(method,path,**kwargs)
        self.api_mock.side_effect=switch;request_sync(actor=self.owner);process_step()
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),0)

    def test_failed_api_read_keeps_previous_records_and_sanitizes_error(self):
        self.sync();self.api_mock.side_effect=fb.BillingError('SECRET TOKEN SHOULD NOT APPEAR');self.sync()
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),4)
        self.assertNotIn('SECRET',str(list(FreshBooksMirrorJob.objects.values_list('error',flat=True))))

    def test_page_budget_keeps_remaining_work_durable(self):
        self.assertEqual(process_mirror(limit=1),1)
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),1)
        self.assertEqual(FreshBooksMirrorJob.objects.filter(state='queued').count(),3)
        self.assertEqual(process_mirror(limit=3),3)

    def test_estimates_require_explicit_existing_scope_and_enablement(self):
        self.connection.granted_scopes.remove('user:estimates:read');self.connection.save()
        with self.assertRaises(ValidationError):save_settings(actor=self.owner,version=self.settings.version,enabled=True,include_estimates=True)
        self.sync();self.assertFalse(FreshBooksMirrorRecord.objects.filter(kind='estimates').exists())

    def test_authorized_estimates_copy_lines_status_and_terms_readonly(self):
        save_settings(actor=self.owner,version=self.settings.version,enabled=True,include_estimates=True)
        self.sync();row=self.record('estimates')
        self.assertEqual(row.source['status'],'accepted');self.assertEqual(row.source['lines'][0]['amount'],'100.00')
        self.assertEqual(FreshBooksInvoice.objects.count(),0)

    def test_readonly_estimate_oauth_preserves_payment_scope(self):
        url=fb.authorization_url('state',with_payments=True,with_estimates=True)
        scopes=parse_qs(urlsplit(url).query)['scope'][0].split()
        self.assertIn('user:estimates:read',scopes);self.assertNotIn('user:estimates:write',scopes)
        self.assertIn('user:payments:read',scopes)

    def test_source_changes_append_history_and_old_revision_is_viewable(self):
        self.sync();record=self.record();old=copy.deepcopy(record.source)
        self.remote['invoices'][0].update(amount=cash('125.00'),outstanding=cash('95.00'));self.sync();record.refresh_from_db()
        self.assertEqual(record.revision,2);self.assertEqual(record.history.get(sequence=1).source,old)
        response=self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[record.pk]),{'revision':1})
        self.assertContains(response,'Historical source revision 1');self.assertContains(response,'100.00')

    def test_manual_identity_link_is_version_checked_and_reuses_existing_client(self):
        target=Client.objects.create(name='Acme Homes LLC',normalized_name='acme homes llc',kind='company')
        ClientContact.objects.create(client=target,email='mike@example.invalid');self.sync();record=self.record('clients')
        link_client(record_id=record.pk,client_id=target.pk,version=record.version,actor=self.owner)
        self.assertEqual(self.record('clients').client_id,target.pk)
        with self.assertRaises(EditConflict):link_client(record_id=record.pk,client_id=target.pk,version=record.version,actor=self.owner)
        self.assertEqual(Client.objects.count(),1)

    def test_mirrored_billing_identity_cannot_be_silently_reassigned(self):
        self.sync();record=self.record('clients')
        other=Client.objects.create(name='Other company',normalized_name='other company',kind='company')
        with self.assertRaises(ValidationError):link_client(record_id=record.pk,client_id=other.pk,version=record.version,actor=self.owner)

    def test_payment_without_explicit_client_uses_verified_invoice_identity(self):
        self.remote['payments'][0]['clientid']=None;self.sync()
        self.assertEqual(self.record('payments').client_id,self.crm().pk)

    def test_payment_with_wrong_invoice_client_is_flagged(self):
        self.remote['payments'][0]['clientid']=999;self.sync()
        self.assertTrue(any('does not agree' in value for value in self.record('payments').issues))

    def test_provider_change_does_not_rewrite_local_project_name_or_create_work(self):
        self.sync();project=WorkProject.objects.create(name='Corrected Lot 12')
        FreshBooksInvoice.objects.create(account_id='TEST123',invoice_id=401,customer_id=101,project=project,client=self.crm(),
            marker='fictional-link',payload={},state='linked')
        self.sync();self.assertEqual(self.record().project_id,project.pk)
        self.remote['invoices'][0]['customerid']=999;self.sync()
        self.assertIsNone(self.record().project_id);project.refresh_from_db();self.assertEqual(project.name,'Corrected Lot 12')
        self.assertEqual(WorkItem.objects.count(),0)

    def test_get_views_are_private_and_never_call_provider(self):
        self.sync();before=self.api_mock.call_count
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(before,self.api_mock.call_count)
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)

    def test_sync_and_settings_are_csrf_and_permission_protected(self):
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.owner)
        self.assertEqual(browser.post(self.url,{'action':'sync'}).status_code,403)
        staff=get_user_model().objects.create_user('mirror-viewer',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(staff);self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,{'action':'sync'}).status_code,403)

    def test_integrity_failure_is_not_displayed_as_valid_money(self):
        self.sync();record=self.record();FreshBooksMirrorRecord.objects.filter(pk=record.pk).update(source_hash='0'*64)
        response=self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[record.pk]));self.assertEqual(response.status_code,409)
        response=self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[record.pk]),{'revision':1});self.assertEqual(response.status_code,200)

    def test_business_scoped_details_do_not_mix_accounts(self):
        self.sync();record=self.record();FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
        self.assertEqual(self.client.get(reverse('workqueue:freshbooks_mirror_detail',args=[record.pk])).status_code,404)

    def test_new_records_appear_under_client_and_workspace_search(self):
        self.sync();client=self.crm()
        response=self.client.get(reverse('workqueue:client_detail',args=[client.pk]))
        self.assertContains(response,'Fictional Birch Lane project');self.assertContains(response,'FB-00401')
        response=self.client.get(reverse('workqueue:staff_search'),{'q':'FB-00401'})
        self.assertContains(response,'FreshBooks source records')
        self.assertContains(response,reverse('workqueue:freshbooks_mirror_detail',args=[self.record().pk]))

    def test_unsafe_html_and_unrecognized_money_are_not_trusted(self):
        raw=source_invoice(notes='<script>alert(1)</script><b>Fictional terms</b>')
        data=normalize(raw,'TEST123','invoices');self.assertNotIn('<script>',data['notes'])
        for value in [True,1.01,'Infinity','12.001']:
            with self.subTest(value=value),self.assertRaises(ValueError):normalize(source_invoice(amount={'amount':value,'code':'USD'}),'TEST123','invoices')

    def test_scope_or_disconnection_stops_sync_without_erasing_records(self):
        self.sync();self.connection.needs_reconnect=True;self.connection.save();before=self.api_mock.call_count
        self.assertEqual(process_mirror(),0);self.assertEqual(self.api_mock.call_count,before)
        self.assertEqual(FreshBooksMirrorRecord.objects.count(),4)
