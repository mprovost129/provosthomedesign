from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import dashboard_billing as billing, dashboard_filing as filing, freshbooks
from .dashboard_models import DashboardBillingSummary
from .dropbox_models import DropboxFiling, ProjectFolder
from .freshbooks_models import FreshBooksConnection
from .freshbooks_project_models import FreshBooksProject
from .models import Attachment, NotificationDelivery, Status, WorkItem
from .services import create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class DashboardUpgradeTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('dashboard-upgrade-owner','owner@example.invalid','test')
        self.client.force_login(self.owner)
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.now=timezone.now()
        self.connection=FreshBooksConnection.objects.create(pk=1,account_id='TEST',business_name='Fictional business',
            encrypted_tokens='fictional-encrypted-placeholder',granted_scopes=['user:invoices:read','user:payments:read'])
        self.configured=patch('workqueue.freshbooks.configured',return_value=True)
        self.configured.start();self.addCleanup(self.configured.stop)

    def job(self,kind='invoices',**changes):
        return DashboardBillingSummary.objects.create(**{**dict(account_id='TEST',kind=kind,
            period=billing.period_for(kind),requested_at=self.now),**changes})

    def invoice(self,**changes):
        return {**dict(invoiceid=7,accountid='TEST',accounting_systemid='TEST',vis_state=0,v3_status='sent',
            currency_code='USD',outstanding={'amount':'100.00','code':'USD'},due_date=timezone.localdate().isoformat()),**changes}

    def payment(self,**changes):
        return {**dict(id=11,logid=11,invoiceid=7,accounting_systemid='TEST',vis_state=0,from_credit=False,
            creditid=None,type='Check',date=timezone.localdate().isoformat(),amount={'amount':'25.00','code':'USD'}),**changes}

    def page(self,kind,rows,page=1,pages=1,total=None):
        return {'response':{'result':{kind:rows,'page':page,'pages':pages,'total':len(rows) if total is None else total,'per_page':100}}}

    def process(self,job,rows,**meta):
        with patch('workqueue.freshbooks.api',return_value=self.page(job.kind,rows,**meta)) as api:
            self.assertTrue(billing.process_one())
        job.refresh_from_db()
        self.assertEqual(api.call_args.args[0],'GET')
        return api

    def test_shortcuts_search_and_open_manual_work_form(self):
        response=self.client.get(reverse('workqueue:dashboard'))
        self.assertContains(response,'Add work')
        self.assertContains(response,'Add client')
        self.assertContains(response,'Find a project')
        self.assertContains(response,'name="scope" value="all"')
        response=self.client.get(reverse('workqueue:queue')+'?add=1')
        self.assertContains(response,'id="add-work" open')

    def test_project_shortcut_finds_update_request_and_dropbox_job_number(self):
        update=create_work(data=work_data(kind='update',project=self.item.project),actor=self.owner)[0]
        ProjectFolder.objects.create(project=self.item.project,number='261007',intended_name='Fictional numbered folder')
        for query in (update.reference,'261007'):
            response=self.client.get(reverse('workqueue:projects'),{'q':query,'scope':'all'})
            self.assertEqual(response.context['page'].paginator.count,1)
            self.assertEqual(response.context['page'][0].pk,self.item.project_id)
            self.assertEqual(response.context['page'][0].job_count,2)

    def test_read_only_staff_cannot_refresh_or_see_mutating_shortcuts(self):
        user=get_user_model().objects.create_user('read-only-dash',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(user)
        response=self.client.get(reverse('workqueue:dashboard'))
        self.assertNotContains(response,'> Add work')
        self.assertNotContains(response,'> Add client')
        self.assertEqual(self.client.post(reverse('workqueue:dashboard_billing_refresh')).status_code,403)
        self.assertFalse(DashboardBillingSummary.objects.exists())

    def test_refresh_is_post_only_csrf_protected_and_only_queues(self):
        url=reverse('workqueue:dashboard_billing_refresh')
        self.assertEqual(self.client.get(url).status_code,405)
        from django.test import Client
        protected=Client(enforce_csrf_checks=True);protected.force_login(self.owner)
        self.assertEqual(protected.post(url).status_code,403)
        with patch('workqueue.freshbooks.api') as api:
            self.assertEqual(self.client.post(url).status_code,302)
        api.assert_not_called()
        self.assertEqual(DashboardBillingSummary.objects.count(),2)

    def test_filing_pending_counts_requests_not_attachments_and_preserves_deleted_work(self):
        for index in range(2):
            Attachment.objects.create(submission=self.item.submissions.first(),original_name=f'plan-{index}.pdf',storage_key=f'fictional/{index}')
        self.assertEqual(filing.filing_summary()['approval'],1)
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertEqual(filing.filing_summary()['approval'],1)  # uploaded documents still need approval
        WorkItem.objects.filter(pk=self.item.pk).update(deleted_at=self.now)
        self.assertEqual(filing.filing_summary()['approval'],0)

    def test_closed_legacy_without_stored_uploads_is_not_pending(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertEqual(filing.filing_summary()['approval'],0)

    def test_filing_states_and_links_match_filtered_inbox(self):
        folder=ProjectFolder.objects.create(project=self.item.project,intended_name='Fictional project folder')
        copy=DropboxFiling.objects.create(submission=self.item.submissions.first(),destination=folder,
            state='copying',batch_name='Fictional upload')
        FreshBooksProject.objects.create(project=self.item.project,work_item=self.item,client=self.item.client_contact.client,
            account_id='TEST',business_id=77,title='Fictional project',marker='fictional-dash-marker',state='unknown')
        counts=filing.filing_summary()
        self.assertEqual(counts['approval'],0)
        self.assertEqual(counts['dropbox_pending'],1)
        self.assertEqual(counts['freshbooks_attention'],1)
        url=reverse('workqueue:dashboard_filing')
        response=self.client.get(url+'?state=freshbooks_attention&q='+self.item.reference)
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,reverse('workqueue:dropbox_review',args=[self.item.pk]))
        self.assertEqual(self.client.get(url+'?state=dropbox_attention').context['page'].paginator.count,0)
        copy.state='attention';copy.save()
        self.assertEqual(self.client.get(url+'?state=dropbox_attention').context['page'].paginator.count,1)

    def test_filing_get_never_creates_folders_projects_or_notifications(self):
        before=(ProjectFolder.objects.count(),FreshBooksProject.objects.count(),NotificationDelivery.objects.count())
        with patch('workqueue.freshbooks.api',side_effect=AssertionError('No remote calls')):
            response=self.client.get(reverse('workqueue:dashboard_filing'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(before,(ProjectFolder.objects.count(),FreshBooksProject.objects.count(),NotificationDelivery.objects.count()))

    def test_checks_are_idempotent_and_throttled(self):
        self.assertEqual(billing.request_checks(self.owner),2)
        self.assertEqual(billing.request_checks(self.owner),0)
        DashboardBillingSummary.objects.all().update(state='idle')
        self.assertEqual(billing.request_checks(self.owner),0)
        DashboardBillingSummary.objects.all().update(requested_at=self.now-timedelta(days=2))
        self.assertEqual(billing.request_checks(automatic=True),2)

    def test_missing_payment_scope_does_not_block_invoice_checks_or_claim_zero_payments(self):
        self.connection.granted_scopes=['user:invoices:read'];self.connection.save()
        self.assertEqual(billing.request_checks(self.owner),1)
        self.assertEqual(DashboardBillingSummary.objects.get().kind,'invoices')
        response=self.client.get(reverse('workqueue:dashboard'))
        self.assertContains(response,'Payment access needed')
        self.assertNotContains(response,'No payments recorded')

    def test_complete_invoice_scan_splits_currency_and_excludes_drafts(self):
        job=self.job()
        self.process(job,[self.invoice(),self.invoice(invoiceid=8,v3_status='overdue',currency_code='CAD',
            outstanding={'amount':'50.00','code':'CAD'}),self.invoice(invoiceid=9,v3_status='draft')])
        self.assertEqual(job.state,'idle')
        self.assertIsNotNone(job.checked_at)
        self.assertEqual(Decimal(job.totals['USD']['outstanding']),100)
        self.assertEqual(Decimal(job.totals['CAD']['overdue']),50)
        self.assertEqual(job.excluded_count,1)
        self.assertEqual(job.seen_ids,[])

    def test_due_dates_are_overdue_only_before_today_and_archived_receivables_count(self):
        job=self.job();today=timezone.localdate()
        self.process(job,[self.invoice(vis_state=2,due_date=(today-timedelta(days=1)).isoformat()),self.invoice(invoiceid=8)])
        self.assertEqual(Decimal(job.totals['USD']['outstanding']),200)
        self.assertEqual(Decimal(job.totals['USD']['overdue']),100)

    def test_payment_date_not_invoice_date_and_credits_are_excluded(self):
        job=self.job('payments')
        api=self.process(job,[self.payment(),self.payment(id=12,logid=12,from_credit=True),
            self.payment(id=13,logid=13,vis_state=1),self.payment(id=14,logid=14,amount={'amount':'-5.00','code':'USD'})])
        self.assertEqual(Decimal(job.totals['USD']['payments']),20)
        self.assertEqual(job.excluded_count,2)
        params=api.call_args.kwargs['params']
        self.assertEqual(params['search[date_min]'],billing.period_for('payments')+'-01')
        self.assertEqual(params['search[date_max]'],(timezone.localdate()+timedelta(days=1)).isoformat())

    def test_real_zero_only_after_a_complete_empty_scan(self):
        job=self.job()
        self.assertIsNone(billing.summary_context()['invoice']['snapshot'].checked_at)
        self.process(job,[],pages=0)
        self.assertEqual(job.state,'idle')
        self.assertEqual(job.totals,{})
        self.assertContains(self.client.get(reverse('workqueue:dashboard')),'No outstanding balances')

    def test_failed_partial_and_duplicate_pages_never_replace_prior_totals(self):
        job=self.job(totals={'USD':{'outstanding':'777.00'}},checked_at=self.now-timedelta(days=2))
        rows=[self.invoice(invoiceid=n) for n in range(1,101)]
        self.process(job,rows,pages=2,total=101)
        self.assertEqual(job.state,'scanning')
        self.assertEqual(job.page,2)
        self.assertEqual(job.totals['USD']['outstanding'],'777.00')
        self.process(job,[self.invoice(invoiceid=1)],page=2,pages=2,total=101)
        self.assertEqual(job.state,'failed')
        self.assertEqual(job.totals['USD']['outstanding'],'777.00')

    def test_bad_identity_money_status_and_pagination_fail_closed(self):
        variants=[self.invoice(accountid='OTHER'),self.invoice(v3_status='mystery'),
            self.invoice(outstanding={'amount':'NaN','code':'USD'}),
            self.invoice(outstanding={'amount':'-1.00','code':'USD'}),
            self.invoice(v3_status='paid'),self.invoice(outstanding={'amount':'1.001','code':'USD'})]
        for raw in variants:
            job=self.job();self.process(job,[raw]);self.assertEqual(job.state,'failed');self.assertIsNone(job.checked_at);job.delete()
        job=self.job();self.process(job,[self.invoice()],total=2)
        self.assertEqual(job.state,'failed')

    def test_provider_failure_retains_snapshot_and_omits_private_errors(self):
        job=self.job(totals={'USD':{'outstanding':'777.00'}},checked_at=self.now)
        with patch('workqueue.freshbooks.api',side_effect=freshbooks.BillingError('PRIVATE-TOKEN-PROVIDER-PAYLOAD')):
            billing.process_one()
        job.refresh_from_db();self.assertEqual(job.state,'failed')
        self.assertEqual(job.totals['USD']['outstanding'],'777.00')
        self.assertNotIn('PRIVATE',job.error)

    def test_business_switch_during_scan_cannot_publish(self):
        job=self.job()
        def switch(*args,**kwargs):
            FreshBooksConnection.objects.filter(pk=1).update(account_id='OTHER')
            return self.page('invoices',[self.invoice()])
        with patch('workqueue.freshbooks.api',side_effect=switch):
            billing.process_one()
        job.refresh_from_db();self.assertEqual(job.state,'failed');self.assertIsNone(job.checked_at)
        self.assertIsNone(billing.summary_context()['invoice']['snapshot'])

    def test_lease_skips_work_in_progress_and_recovers_expired_read(self):
        job=self.job(lease_until=self.now+timedelta(minutes=2),state='scanning')
        with patch('workqueue.freshbooks.api') as api:
            self.assertFalse(billing.process_one())
        api.assert_not_called()
        job.lease_until=self.now-timedelta(minutes=1);job.save()
        self.process(job,[])
        self.assertEqual(job.state,'idle')

    def test_old_payment_month_is_not_shown_as_current_month(self):
        job=self.job('payments',period='2020-01',totals={'USD':{'payments':'999.00'}},checked_at=self.now)
        self.assertIsNone(billing.summary_context()['payment']['snapshot'])
        with patch('workqueue.freshbooks.api') as api:
            billing.process_one()
        api.assert_not_called()
        job.refresh_from_db();self.assertEqual(job.state,'failed')

    @override_settings(FRESHBOOKS_CLIENT_ID='fictional-preview-id')
    def test_oauth_payment_scope_is_explicit_and_read_only(self):
        with patch('workqueue.freshbooks.cipher'),patch('workqueue.freshbooks.redirect_uri',return_value='https://example.invalid/callback'):
            default=parse_qs(urlsplit(freshbooks.authorization_url('state')).query)['scope'][0].split()
            extra=parse_qs(urlsplit(freshbooks.authorization_url('state',with_payments=True)).query)['scope'][0].split()
        self.assertNotIn('user:payments:read',default)
        self.assertIn('user:payments:read',extra)
        self.assertNotIn('user:payments:write',extra)

    def test_public_cannot_access_filing_or_refresh(self):
        self.client.logout()
        for name in ('dashboard_filing','dashboard_billing_refresh'):
            response=self.client.get(reverse('workqueue:'+name))
            self.assertEqual(response.status_code,302)
            self.assertIn('/staff/login/',response.url)
