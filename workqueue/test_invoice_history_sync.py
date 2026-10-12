from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Client, ClientContact, WorkItem, NotificationDelivery
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink
from .invoice_history_models import HistoricalInvoice, HistoricalInvoiceBalance, InvoiceHistorySync
from .invoice_history_sync import request_sync, process_step, sync_history, live_context, contact_suggestions
from . import test_invoice_csv as csv_tests


def remote(number='0001', invoice_id=10, **changes):
    return {'accountid':'TEST123','accounting_systemid':'TEST123','invoiceid':invoice_id,'id':invoice_id,
            'invoice_number':number,'customerid':20,'create_date':'2026-10-01','due_date':'2026-10-20',
            'currency_code':'USD','v3_status':'paid','vis_state':0,
            'amount':{'amount':'150.00','code':'USD'},'paid':{'amount':'150.00','code':'USD'},
            'outstanding':{'amount':'0.00','code':'USD'},**changes}


def page(rows, page=1, pages=1, total=None):
    return {'response':{'result':{'invoices':rows,'page':page,'pages':pages,'per_page':100,'total':len(rows) if total is None else total}}}


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class HistorySyncTests(TestCase):
    # Reuse fixture helpers without inheriting/rerunning the CSV test suite.
    batch = csv_tests.InvoiceCSVTests.batch
    apply = csv_tests.InvoiceCSVTests.apply

    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('history-sync-owner','owner@example.invalid','test')
        self.connection=FreshBooksConnection.objects.create(pk=1,account_id='TEST123',business_name='Test business',encrypted_tokens='fake-test-only')
        self.client.force_login(self.owner)
        self.config=patch('workqueue.freshbooks.configured',return_value=True);self.config.start();self.addCleanup(self.config.stop)
        self.apply(self.batch())
        self.invoice=HistoricalInvoice.objects.get()

    def run_scan(self, rows=None):
        job=request_sync(self.owner)
        with patch('workqueue.freshbooks.api',return_value=page(rows if rows is not None else [remote()])) as api:
            self.assertTrue(process_step())
            api.assert_called_once_with('GET','/accounting/account/TEST123/invoices/invoices',params={'page':1,'per_page':100})
        return job

    def test_success_preserves_exported_facts_and_no_financial_or_queue_writes(self):
        before=(self.invoice.lines,self.invoice.line_total,self.invoice.exported_status,WorkItem.objects.count(),NotificationDelivery.objects.count())
        self.run_scan([remote(v3_status='partial',paid={'amount':'50.00','code':'USD'},outstanding={'amount':'100.00','code':'USD'})])
        balance=HistoricalInvoiceBalance.objects.get();self.assertEqual(balance.outstanding,100);self.assertEqual(balance.status,'partial')
        self.assertIsNotNone(balance.verified_at);self.assertEqual(balance.provider_invoice_id,10)
        self.invoice.refresh_from_db()
        self.assertEqual(before,(self.invoice.lines,self.invoice.line_total,self.invoice.exported_status,WorkItem.objects.count(),NotificationDelivery.objects.count()))
        self.assertIn('/TEST123-10',live_context(self.invoice)['url'])

    def test_complete_pagination_required_before_any_snapshot_applies(self):
        job=request_sync(self.owner)
        rows=[remote()]+[remote(f'OTHER{i}',i+100) for i in range(99)]
        with patch('workqueue.freshbooks.api',return_value=page(rows,pages=2,total=101)):
            process_step()
        self.assertFalse(HistoricalInvoiceBalance.objects.exists())
        with patch('workqueue.freshbooks.api',return_value=page([remote('OTHER-LAST',1000)],page=2,pages=2,total=101)):
            process_step()
        self.assertEqual(HistoricalInvoiceBalance.objects.get().provider_invoice_id,10)
        job.refresh_from_db();self.assertEqual(job.result['verified'],1)

    def test_pagination_change_aborts_without_false_freshness_or_partial_apply(self):
        balance=HistoricalInvoiceBalance.objects.create(invoice=self.invoice,provider_invoice_id=10,verified_at=timezone.now()-timedelta(days=3),amount=150,outstanding=25)
        original=balance.verified_at
        job=request_sync(self.owner)
        rows=[remote()]+[remote(f'OTHER{i}',i+100) for i in range(99)]
        with patch('workqueue.freshbooks.api',return_value=page(rows,pages=2,total=101)):process_step()
        with patch('workqueue.freshbooks.api',return_value=page([],page=2,pages=1,total=100)):process_step()
        job.refresh_from_db();balance.refresh_from_db()
        self.assertEqual(job.state,'failed');self.assertEqual(balance.outstanding,25);self.assertEqual(balance.verified_at,original)
        self.assertTrue(live_context(self.invoice)['stale'])

    def test_invalid_identity_currency_date_money_and_deletion_hold_for_review(self):
        changes=[{'accountid':'OTHER'},{'accounting_systemid':'OTHER'},{'id':11},{'customerid':0},{'vis_state':1},
            {'create_date':'2026-09-30'},{'currency_code':'CAD'}, {'v3_status':'<script>'},
            {'amount':{'amount':'NaN','code':'USD'}},{'paid':{'amount':'1.001','code':'USD'}},
            {'outstanding':{'amount':'100','code':'CAD'}},{'due_date':'not-date'}]
        for change in changes:
            InvoiceHistorySync.objects.all().delete();HistoricalInvoiceBalance.objects.all().delete()
            self.run_scan([remote(**change)])
            balance=HistoricalInvoiceBalance.objects.get();self.assertIsNone(balance.verified_at);self.assertTrue(balance.error)
            self.assertIsNone(live_context(self.invoice)['url'])

    def test_duplicate_invoice_number_never_chooses_first_match(self):
        self.run_scan([remote(),remote(invoice_id=11)])
        balance=HistoricalInvoiceBalance.objects.get();self.assertIn('Multiple',balance.error);self.assertIsNone(balance.provider_invoice_id)

    def test_missing_records_are_not_marked_paid_and_previous_snapshot_is_retained(self):
        old=timezone.now()-timedelta(hours=2)
        balance=HistoricalInvoiceBalance.objects.create(invoice=self.invoice,provider_invoice_id=10,verified_at=old,outstanding=70)
        self.run_scan([]);balance.refresh_from_db()
        self.assertEqual(balance.outstanding,70);self.assertEqual(balance.verified_at,old);self.assertIn('Not found',balance.error)
        self.assertTrue(live_context(self.invoice)['stale'])

    def test_empty_provider_account_is_a_valid_completed_scan(self):
        job=request_sync(self.owner)
        with patch('workqueue.freshbooks.api',return_value=page([],pages=0,total=0)):process_step()
        job.refresh_from_db();self.assertEqual(job.state,'complete');self.assertEqual(job.result['review'],1)

    def test_existing_provider_id_cannot_silently_change(self):
        HistoricalInvoiceBalance.objects.create(invoice=self.invoice,provider_invoice_id=99)
        self.run_scan();balance=HistoricalInvoiceBalance.objects.get()
        self.assertEqual(balance.provider_invoice_id,99);self.assertTrue(balance.error)

    def test_archived_invoices_still_verify(self):
        self.run_scan([remote(vis_state=2)])
        self.assertTrue(HistoricalInvoiceBalance.objects.get().archived)

    def test_lease_prevents_duplicate_processing_and_expired_lease_recovers(self):
        job=request_sync(self.owner)
        InvoiceHistorySync.objects.filter(pk=job.pk).update(lease_until=timezone.now()+timedelta(minutes=1))
        with patch('workqueue.freshbooks.api') as api:self.assertFalse(process_step());api.assert_not_called()
        InvoiceHistorySync.objects.filter(pk=job.pk).update(lease_until=timezone.now()-timedelta(seconds=1))
        with patch('workqueue.freshbooks.api',return_value=page([remote()])):self.assertTrue(process_step())

    def test_enqueue_dedup_cooldown_and_daily_schedule(self):
        job=request_sync(self.owner);self.assertEqual(request_sync(self.owner).pk,job.pk)
        job.state='complete';job.finished_at=timezone.now();job.save()
        self.assertEqual(request_sync(self.owner).pk,job.pk)
        InvoiceHistorySync.objects.filter(pk=job.pk).update(created_at=timezone.now()-timedelta(hours=23))
        self.assertEqual(request_sync(automatic=True).pk,job.pk)
        InvoiceHistorySync.objects.filter(pk=job.pk).update(created_at=timezone.now()-timedelta(hours=25))
        self.assertNotEqual(request_sync(automatic=True).pk,job.pk)

    def test_failed_scan_backs_off_without_provider_response_leaks(self):
        from .freshbooks import BillingError
        job=request_sync(self.owner)
        with patch('workqueue.freshbooks.api',side_effect=BillingError('SECRET PROVIDER CONTENT')):process_step()
        job.refresh_from_db();self.assertEqual(job.state,'failed');self.assertNotIn('SECRET',job.error)
        InvoiceHistorySync.objects.filter(pk=job.pk).update(created_at=timezone.now()-timedelta(minutes=20))
        self.assertEqual(request_sync(automatic=True).pk,job.pk)

    def test_disconnection_or_disabled_configuration_makes_no_provider_calls(self):
        self.connection.needs_reconnect=True;self.connection.save()
        with patch('workqueue.freshbooks.api') as api:self.assertEqual(sync_history(),0);api.assert_not_called()
        self.connection.needs_reconnect=False;self.connection.save()
        with patch('workqueue.freshbooks.configured',return_value=False),patch('workqueue.freshbooks.api') as api:
            self.assertEqual(sync_history(),0);api.assert_not_called()

    def test_business_change_during_network_request_cannot_apply(self):
        job=request_sync(self.owner)
        def changed(*args,**kwargs):
            FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER');return page([remote()])
        with patch('workqueue.freshbooks.api',side_effect=changed):process_step()
        self.assertFalse(HistoricalInvoiceBalance.objects.exists());job.refresh_from_db();self.assertEqual(job.state,'failed')

    def test_hidden_invoice_during_scan_is_not_restored_or_updated(self):
        request_sync(self.owner)
        def hidden(*args,**kwargs):
            HistoricalInvoice.objects.filter(pk=self.invoice.pk).update(removed_at=timezone.now());return page([remote()])
        with patch('workqueue.freshbooks.api',side_effect=hidden):process_step()
        self.assertFalse(HistoricalInvoiceBalance.objects.exists())

    def test_contact_profiles_suggest_unique_email_but_never_auto_assign(self):
        client=Client.objects.create(name='Existing office',normalized_name='existing office')
        ClientContact.objects.create(client=client,email='billing@example.invalid',full_name='Office')
        job=self.run_scan()
        raw={'id':20,'accounting_systemid':'TEST123','vis_state':0,'email':'BILLING@example.invalid','organization':'Export company'}
        with patch('workqueue.freshbooks.api',return_value={'response':{'result':{'client':raw}}}) as api:process_step()
        api.assert_called_once_with('GET','/accounting/account/TEST123/users/clients/20')
        self.invoice.refresh_from_db();self.assertIsNone(self.invoice.client_id)
        self.assertEqual([c.pk for c in contact_suggestions(self.invoice)],[client.pk])
        job.refresh_from_db();self.assertEqual(job.state,'complete')
        self.assertEqual(ClientContact.objects.count(),1)
        response=self.client.get(reverse('workqueue:invoice_history_detail',args=[self.invoice.pk]))
        self.assertContains(response,'Select Existing office');self.assertContains(response,'Suggested CRM matches')

    def test_invalid_or_deleted_billing_profiles_cannot_suggest_clients(self):
        self.run_scan()
        with patch('workqueue.freshbooks.api',return_value={'response':{'result':{'client':{'id':20,'accounting_systemid':'OTHER','vis_state':0}}}}):process_step()
        self.invoice.refresh_from_db();self.assertEqual(contact_suggestions(self.invoice),[])
        self.assertIsNone(HistoricalInvoiceBalance.objects.get().contact_verified_at)

    def test_view_is_private_readonly_and_refresh_is_csrf_post_staff_only(self):
        url=reverse('workqueue:invoice_history_check')
        self.assertEqual(self.client.get(url).status_code,405)
        from django.test import Client as BrowserClient
        secure=BrowserClient(enforce_csrf_checks=True);secure.force_login(self.owner)
        self.assertEqual(secure.post(url).status_code,403)
        reader=get_user_model().objects.create_user('history-balance-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(reader);self.assertEqual(self.client.post(url).status_code,403)
        response=self.client.get(reverse('workqueue:invoice_history'))
        self.assertEqual(response.status_code,200);self.assertNotContains(response,'Check FreshBooks now')
        self.assertIn('no-store',response['Cache-Control'])
        self.client.logout();self.assertEqual(self.client.get(reverse('workqueue:invoice_history')).status_code,302)

    def test_refresh_post_queues_but_never_calls_provider_or_sends_email(self):
        with patch('workqueue.freshbooks.api') as api:
            self.assertEqual(self.client.post(reverse('workqueue:invoice_history_check')).status_code,302)
            self.client.get(reverse('workqueue:invoice_history'))
        api.assert_not_called();self.assertEqual(InvoiceHistorySync.objects.count(),1);self.assertFalse(NotificationDelivery.objects.exists())

    def test_balance_rendering_stale_times_and_correct_staff_invoice_link(self):
        self.run_scan()
        response=self.client.get(reverse('workqueue:invoice_history'))
        self.assertContains(response,'FreshBooks: paid');self.assertContains(response,'Balance due');self.assertContains(response,'https://my.freshbooks.com/#/invoice/TEST123-10')
        self.assertContains(response,'not current invoice balances')
        HistoricalInvoiceBalance.objects.update(verified_at=timezone.now()-timedelta(days=2))
        self.assertContains(self.client.get(reverse('workqueue:invoice_history')),'Needs a new check')

    def test_balance_due_filter_uses_provider_amounts_and_excludes_drafts_and_stale_totals(self):
        self.run_scan([remote(v3_status='partial',outstanding={'amount':'70.00','code':'USD'})])
        url=reverse('workqueue:invoice_history')
        response=self.client.get(url,{'live':'due'})
        self.assertEqual(response.context['summary']['total'],1)
        self.assertEqual(response.context['live_totals'][0]['balance_due'],70)
        self.assertEqual(self.client.get(url,{'live':'review'}).context['summary']['total'],0)
        HistoricalInvoiceBalance.objects.update(verified_at=timezone.now()-timedelta(days=2))
        response=self.client.get(url,{'live':'due'})
        self.assertEqual(response.context['summary']['total'],1);self.assertEqual(response.context['live_totals'],[])
        self.assertEqual(self.client.get(url,{'live':'review'}).context['summary']['total'],1)
        HistoricalInvoiceBalance.objects.update(status='draft')
        self.assertEqual(self.client.get(url,{'live':'due'}).context['summary']['total'],0)

    def test_worker_budget_limits_reads_and_keeps_remaining_work_durable(self):
        with patch('workqueue.freshbooks.api',side_effect=[page([remote()]),{'response':{'result':{'client':{'id':20,'accounting_systemid':'TEST123','vis_state':0,'email':'','organization':'Office'}}}}]) as api:
            self.assertEqual(sync_history(limit=1),1);self.assertEqual(api.call_count,1)
            self.assertEqual(InvoiceHistorySync.objects.get().state,'contacts')
            self.assertEqual(sync_history(limit=1),1);self.assertEqual(api.call_count,2)
        self.assertEqual(InvoiceHistorySync.objects.get().state,'complete')
