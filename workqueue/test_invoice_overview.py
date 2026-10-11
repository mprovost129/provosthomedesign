from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, Status, NotificationDelivery
from .pm_models import Proposal, ProposalRevision, ProjectEvent
from .freshbooks_models import FreshBooksConnection, FreshBooksInvoice
from .invoice_overview import invoice_queryset, invoice_row
from .services import create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class InvoiceOverviewTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('invoice-owner','owner@example.invalid','test')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.project=self.item.project
        self.client.force_login(self.owner)
        self.url=reverse('workqueue:invoice_overview')
        self.now=timezone.now()
        self.index=0

    def invoice(self, status='sent', **changes):
        self.index+=1
        proposal=Proposal.objects.create(project=self.project,client=self.item.client_contact.client,
            number=f'EST-{self.index:05d}',title=f'Design package {self.index}',has_draft=False)
        revision=ProposalRevision.objects.create(proposal=proposal,sequence=1,state='accepted',
            snapshot={},recipient='invoice@example.invalid')
        proposal.current_revision=revision;proposal.save()
        data=dict(proposal=proposal,revision=revision,account_id='0VZEky',customer_id=7,
            marker=proposal.number,payload={'secret':'PRIVATE-PAYLOAD'},state='linked',
            invoice_id=self.index,invoice_number=f'INV-{self.index:04d}',billing_status=status,
            currency='USD',amount=Decimal('100'),paid=Decimal('0'),outstanding=Decimal('100'),last_sync_at=self.now)
        return FreshBooksInvoice.objects.create(**{**data,**changes})

    def row(self, invoice):
        return invoice_row(invoice_queryset(now=self.now).get(pk=invoice.pk))

    def test_multiple_jobs_and_old_revisions_do_not_multiply_invoices_or_balances(self):
        invoice=self.invoice()
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        ProposalRevision.objects.create(proposal=invoice.proposal,sequence=2,snapshot={},recipient='old@example.invalid')
        response=self.client.get(self.url)
        self.assertEqual(response.context['summary']['total'],1)
        self.assertEqual(response.context['balances'][0]['saved_outstanding'],100)
        self.assertEqual(len(response.context['rows']),1)
        self.assertNotContains(response,'old@example.invalid')

    def test_status_buckets_distinguish_draft_receivable_overdue_paid_and_closed(self):
        for status,changes,expected in [
            ('draft',{},'draft'),('created',{},'draft'),('sent',{},'outstanding'),
            ('viewed',{},'outstanding'),('partial',{'paid':25,'outstanding':75},'outstanding'),
            ('deposit-partial',{'paid':25,'outstanding':75},'outstanding'),('overdue',{},'overdue'),
            ('paid',{'paid':100,'outstanding':0},'paid'),('autopaid',{'paid':100,'outstanding':0},'paid'),
            ('deleted',{},'closed'),('voided',{},'closed'),('mystery',{},'review'),
            ('disputed',{},'review'),('failed',{},'review'),('retry',{},'review'),
            ('success',{'paid':100,'outstanding':0},'review')]:
            self.assertEqual(self.row(self.invoice(status,**changes))['invoice'].billing_bucket,expected,status)

    def test_preparing_and_ambiguous_or_failed_creates_never_become_receivables(self):
        for state in ['queued','creating','unknown','failed']:
            row=self.row(self.invoice(state=state,invoice_id=None))
            self.assertEqual(row['invoice'].billing_bucket,'preparing' if state in ['queued','creating'] else 'review')
            self.assertFalse(row['invoice'].receivable)
            self.assertEqual(row['url'],'')
        self.assertEqual(self.client.get(self.url).context['balances'],[])

    def test_totals_separate_currencies_and_exclude_drafts_closed_and_incomplete_records(self):
        self.invoice(outstanding=75,paid=25)
        self.invoice('overdue',currency='CAD',outstanding=20,amount=20)
        self.invoice('draft');self.invoice('deleted');self.invoice('paid',paid=100,outstanding=0)
        self.invoice('disputed');self.invoice(outstanding=None)
        self.invoice(currency='?')
        response=self.client.get(self.url)
        totals={r['currency_code']:r for r in response.context['balances']}
        self.assertEqual(set(totals),{'USD','CAD'})
        self.assertEqual(totals['USD']['saved_outstanding'],75)
        self.assertEqual(totals['CAD']['overdue'],20)
        self.assertEqual(response.context['summary']['overdue'],1)

    def test_missing_balances_and_conflicting_paid_status_need_review(self):
        for changes in [{'amount':None},{'paid':None},{'outstanding':None},{'invoice_id':None},
                        {'paid':-1},{'amount':-1},{'outstanding':-1},{'last_sync_at':None}]:
            row=self.row(self.invoice(**changes))
            self.assertTrue(row['invoice'].needs_review,changes)
            self.assertEqual(row['invoice'].billing_bucket,'review')
        self.assertEqual(self.row(self.invoice('paid',outstanding=1))['invoice'].billing_bucket,'review')

    def test_stale_boundary_and_recent_failed_refresh_are_not_claimed_fresh(self):
        edge=self.invoice(last_sync_at=self.now-timedelta(hours=24))
        older=self.invoice(last_sync_at=self.now-timedelta(hours=24,seconds=1))
        failed=self.invoice(last_error='PRIVATE-API-ERROR',last_sync_at=self.now)
        self.assertFalse(self.row(edge)['invoice'].stale)
        self.assertTrue(self.row(older)['invoice'].stale)
        self.assertTrue(self.row(failed)['invoice'].needs_review)
        with patch('workqueue.invoice_overview.timezone.now',return_value=self.now):
            response=self.client.get(self.url)
        self.assertContains(response,'may be from an earlier successful check')
        self.assertEqual(response.context['balances'][0]['unverified'],2)
        self.assertNotContains(response,'PRIVATE-API-ERROR')

    def test_saved_overdue_status_is_authoritative_and_case_is_normalized(self):
        invoice=self.invoice(' OvErDuE ',currency='usd')
        row=self.row(invoice)
        self.assertEqual(row['invoice'].billing_bucket,'overdue')
        self.assertEqual(row['currency'],'USD')
        self.assertEqual(row['remote_label'],'Overdue')
        self.assertEqual(self.row(self.invoice('sent'))['invoice'].billing_bucket,'outstanding')

    def test_search_matches_invoice_proposal_project_client_job_email(self):
        invoice=self.invoice()
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        for query in [invoice.invoice_number,invoice.proposal.number,invoice.proposal.title,self.project.name,
            self.project.street,self.item.reference,self.item.contact_email,invoice.revision.recipient,invoice.proposal.client.name]:
            self.assertEqual(invoice_queryset(query).count(),1,query)
        self.assertFalse(invoice_queryset('does-not-exist').exists())

    def test_active_scope_and_all_history_after_completed_and_removed_jobs(self):
        self.invoice()
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertFalse(invoice_queryset(scope='active').exists())
        self.assertEqual(invoice_queryset().count(),1)
        sibling=create_work(data=work_data(kind='update',project=self.project),actor=self.owner)[0]
        self.assertEqual(invoice_queryset(scope='active').count(),1)
        WorkItem.all_objects.filter(project=self.project).update(deleted_at=self.now)
        self.assertFalse(invoice_queryset(scope='active').exists())
        self.assertEqual(invoice_queryset().count(),1)

    def test_filters_do_not_change_summary_balances_and_validate_parameters(self):
        self.invoice();self.invoice('draft');self.invoice('overdue')
        response=self.client.get(self.url,{'state':'draft'})
        self.assertEqual(response.context['summary']['total'],3)
        self.assertEqual(response.context['balances'][0]['saved_outstanding'],200)
        self.assertEqual(response.context['page'].paginator.count,1)
        response=self.client.get(self.url,{'state':'bogus','scope':'bogus','page':'nonsense','q':'x'*250})
        self.assertEqual(response.context['state'],'all');self.assertEqual(response.context['scope'],'all')
        self.assertEqual(len(response.context['q']),200)

    def test_attention_summary_matches_overdue_stale_and_review_records(self):
        self.invoice('overdue')
        self.invoice(last_sync_at=self.now-timedelta(days=2))
        self.invoice(state='unknown',invoice_id=None)
        self.invoice('paid',outstanding=0,paid=100)
        self.invoice('draft')
        response=self.client.get(self.url,{'state':'attention'})
        self.assertEqual(response.context['summary']['need_attention'],3)
        self.assertEqual(response.context['page'].paginator.count,3)
        self.assertContains(response,'Need attention')

    def test_completed_review_projects_without_invoice_and_drafts_but_not_settled_link(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertEqual(self.client.get(self.url).context['review_total'],1)
        invoice=self.invoice('draft')
        self.assertEqual(self.client.get(self.url).context['review_total'],1)
        self.assertEqual(self.client.get(self.url,{'state':'paid'}).context['review_total'],1)
        self.assertEqual(self.client.get(self.url,{'scope':'active'}).context['review_total'],0)
        FreshBooksInvoice.objects.filter(pk=invoice.pk).update(billing_status='paid',outstanding=0,paid=100)
        self.assertEqual(self.client.get(self.url).context['review_total'],0)

    def test_project_review_list_is_limited_and_search_follows_project_fields(self):
        for index in range(12):
            item=create_work(data=work_data(project_name=f'Review {index}',contact_email=f'review{index}@example.invalid'),actor=self.owner)[0]
            WorkItem.objects.filter(pk=item.pk).update(status=Status.COMPLETED)
        response=self.client.get(self.url)
        self.assertEqual(response.context['review_total'],12)
        self.assertEqual(len(response.context['review_projects']),10)
        self.assertContains(response,'Showing the first 10')
        self.assertEqual(self.client.get(self.url,{'q':'Review 11'}).context['review_total'],1)

    def test_links_include_business_account_and_reject_unsafe_identifiers(self):
        invoice=self.invoice()
        self.assertEqual(self.row(invoice)['url'],f'https://my.freshbooks.com/#/invoice/0VZEky-{invoice.invoice_id}')
        FreshBooksInvoice.objects.filter(pk=invoice.pk).update(account_id='bad/account?')
        self.assertEqual(self.row(invoice)['url'],'')
        FreshBooksInvoice.objects.filter(pk=invoice.pk).update(account_id='')
        self.assertEqual(self.row(invoice)['url'],'')

    def test_html_escaping_secrets_and_query_efficiency(self):
        invoice=self.invoice(invoice_number='<script>alert(1)</script>',last_error='PRIVATE-API-ERROR')
        response=self.client.get(self.url,{'q':'<script>'})
        self.assertContains(response,'&lt;script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertNotContains(response,'PRIVATE-API-ERROR');self.assertNotContains(response,'PRIVATE-PAYLOAD')
        with self.assertNumQueries(1):
            rows=[invoice_row(i) for i in invoice_queryset()]
        self.assertEqual(len(rows),1)

    def test_pagination_is_stable_and_reads_have_no_external_calls_or_writes(self):
        for index in range(31):self.invoice()
        before=(FreshBooksInvoice.objects.count(),NotificationDelivery.objects.count(),ProjectEvent.objects.count())
        with patch('workqueue.freshbooks.api') as api,patch('workqueue.freshbooks.wire') as wire:
            first=self.client.get(self.url);second=self.client.get(self.url,{'page':2})
            self.assertEqual(self.client.post(self.url,{'action':'create'}).status_code,405)
        api.assert_not_called();wire.assert_not_called()
        self.assertEqual(len(first.context['rows']),30);self.assertEqual(len(second.context['rows']),1)
        self.assertEqual(first.context['summary']['total'],31)
        self.assertEqual(first.context['balances'][0]['saved_outstanding'],3100)
        self.assertFalse({r['invoice'].pk for r in first.context['rows']} & {r['invoice'].pk for r in second.context['rows']})
        self.assertEqual(before,(FreshBooksInvoice.objects.count(),NotificationDelivery.objects.count(),ProjectEvent.objects.count()))

    def test_staff_permissions_feature_gate_and_private_cache(self):
        self.invoice()
        self.assertIn('no-store',self.client.get(self.url)['Cache-Control'])
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)
        viewer=get_user_model().objects.create_user('invoice-viewer',is_staff=True)
        self.client.force_login(viewer);self.assertEqual(self.client.get(self.url).status_code,403)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.assertEqual(self.client.get(self.url).status_code,200)
        nonstaff=get_user_model().objects.create_user('invoice-nonstaff')
        nonstaff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(nonstaff);self.assertEqual(self.client.get(self.url).status_code,302)
        with override_settings(WORK_QUEUE_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code,404)

    def test_connection_readiness_without_exposing_credentials(self):
        response=self.client.get(self.url)
        self.assertFalse(response.context['connection_ready'])
        FreshBooksConnection.objects.create(pk=1,account_id='0VZEky',encrypted_tokens='PRIVATE-CREDENTIAL')
        with override_settings(FRESHBOOKS_ENABLED=True):
            response=self.client.get(self.url)
            self.assertTrue(response.context['connection_ready']);self.assertNotContains(response,'PRIVATE-CREDENTIAL')
            FreshBooksConnection.objects.filter(pk=1).update(needs_reconnect=True)
            self.assertFalse(self.client.get(self.url).context['connection_ready'])
