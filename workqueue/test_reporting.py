from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, WorkProject, Status
from .pm_models import ProjectPlan, Proposal, ProposalRevision
from .freshbooks_models import FreshBooksInvoice
from .time_models import TimeEntry
from .reporting import project_report_queryset, report_row, OVER_ESTIMATE, BILLING_REVIEW
from .services import create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class ProjectReportTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('report-owner', 'owner@example.invalid', 'test')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.project = self.item.project
        self.url = reverse('workqueue:project_reports')
        self.client.force_login(self.owner)

    def time(self, seconds, **changes):
        data = dict(project=self.project, actor=self.owner, duration_seconds=seconds)
        data.update(changes)
        return TimeEntry.objects.create(**data)

    def invoice(self, number='EST-900', **changes):
        proposal = Proposal.objects.create(project=self.project, client=self.item.client_contact.client,
            number=number, title='Report test', draft={})
        revision = ProposalRevision.objects.create(proposal=proposal, sequence=1, snapshot={}, recipient='owner@example.invalid')
        data = dict(proposal=proposal, revision=revision, account_id='test', customer_id=1, marker=number, payload={})
        data.update(changes)
        return FreshBooksInvoice.objects.create(**data)

    def row(self):
        return report_row(project_report_queryset().get(pk=self.project.pk), [])

    def test_independent_totals_do_not_multiply_by_jobs_or_invoices(self):
        create_work(data=work_data(kind='update', project=self.project), actor=self.owner)
        self.time(3600)
        self.time(3600, category='revision', billable=False)
        self.invoice()
        self.invoice('EST-901')
        response = self.client.get(self.url+'?scope=all')
        row = response.context['rows'][0]
        self.assertEqual(row['project'].job_count, 2)
        self.assertEqual(row['total'], Decimal('2.00'))
        self.assertEqual(row['billable'], Decimal('1.00'))
        self.assertEqual(row['nonbillable'], Decimal('1.00'))
        self.assertEqual(row['revisions'], Decimal('1.00'))
        self.assertEqual(len(row['invoices']), 2)
        self.assertEqual(response.context['summary']['hours'], Decimal('2.00'))

    def test_missing_and_zero_estimate_and_exact_second_overrun(self):
        self.assertIsNone(self.row()['estimated'])
        self.assertFalse(self.row()['over_estimate'])
        plan = ProjectPlan.objects.create(project=self.project, estimated_hours=0)
        self.assertEqual(self.row()['estimated'], 0)
        self.assertFalse(self.row()['over_estimate'])
        plan.estimated_hours = Decimal('1.00')
        plan.save()
        self.time(3601)
        self.assertTrue(self.row()['over_estimate'])  # Display rounds to 1.00; flag uses exact seconds.
        self.assertEqual(project_report_queryset().filter(OVER_ESTIMATE).count(), 1)
        self.assertEqual(self.client.get(self.url+'?attention=over').context['summary']['over'], 1)

    def test_removed_and_running_time_excluded_but_deleted_job_time_remains_recorded(self):
        self.time(300, work_item=self.item)
        self.time(9999, deleted_at=timezone.now())
        self.time(None, source='timer', started_at=timezone.now())
        self.assertEqual(self.row()['total'], Decimal('.08'))
        sibling = create_work(data=work_data(kind='update', project=self.project), actor=self.owner)[0]
        WorkItem.all_objects.filter(pk=self.item.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.row()['project'].job_count, 1)
        self.assertEqual(self.row()['total'], Decimal('.08'))
        WorkItem.all_objects.filter(pk=sibling.pk).update(deleted_at=timezone.now())
        self.assertFalse(project_report_queryset().exists())

    def test_completed_requires_completed_work_and_no_active_job(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.CANCELED)
        self.assertFalse(self.row()['completed'])
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertTrue(self.row()['billing_review'])
        sibling = create_work(data=work_data(kind='update', project=self.project), actor=self.owner)[0]
        self.assertFalse(self.row()['completed'])
        WorkItem.objects.filter(pk=sibling.pk).update(status=Status.DECLINED)
        self.assertTrue(self.row()['completed'])
        self.assertEqual(project_report_queryset().filter(BILLING_REVIEW).count(), 1)

    def test_billing_drafts_failures_and_snapshots_with_separate_currencies(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        invoice = self.invoice(state='linked', invoice_id=99, invoice_number='99', billing_status='draft',
            outstanding=Decimal('150'), currency='USD', last_sync_at=timezone.now()-timedelta(days=2))
        self.assertTrue(self.row()['billing_review'])
        FreshBooksInvoice.objects.filter(pk=invoice.pk).update(billing_status='sent')
        self.assertFalse(self.row()['billing_review'])
        self.invoice('EST-901', state='failed', currency='CAD')
        self.assertTrue(self.row()['billing_review'])
        response = self.client.get(self.url+'?scope=completed&attention=billing')
        self.assertContains(response, 'Outstanding USD 150.00')
        self.assertContains(response, 'Snapshot over 24 hours old')
        self.assertContains(response, 'Needs review')
        self.assertContains(response, 'invoices created separately in FreshBooks')

    def test_search_and_filters_do_not_change_global_project_totals(self):
        self.time(3600)
        create_work(data=work_data(kind='update', project=self.project), actor=self.owner)
        for search in ('Morgan', 'morgan@example.com', self.item.reference, '7 Park'):
            response = self.client.get(self.url, {'q':search, 'scope':'all'})
            self.assertEqual(response.context['summary']['projects'], 1)
            self.assertEqual(response.context['summary']['hours'], Decimal('1.00'))
        self.assertContains(self.client.get(self.url, {'q':'<script>alert(1)</script>'}), '&lt;script&gt;')
        response = self.client.get(self.url+'?scope=bogus&attention=bogus&page=999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['scope'], 'active')

    def test_orphan_and_empty_project_are_safe_and_excluded(self):
        WorkItem.objects.filter(pk=self.item.pk).update(project=None, project_name='')
        WorkProject.objects.create(name='Empty project')
        response = self.client.get(self.url+'?scope=all')
        self.assertContains(response, 'No projects match')
        self.assertEqual(response.context['summary']['hours'], 0)

    def test_permissions_cache_and_get_only(self):
        response = self.client.get(self.url)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        staff = get_user_model().objects.create_user('report-staff', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    @override_settings(WORK_QUEUE_ENABLED=False)
    def test_feature_switch(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_pagination_limits_invoice_loading_and_preserves_filtered_summary(self):
        for index in range(31):
            project = WorkProject.objects.create(name=f'Example {index:02}')
            create_work(data=work_data(project=project, kind='update'), actor=self.owner)
            self.time(3600, project=project)
        response = self.client.get(self.url+'?scope=all&page=2')
        self.assertEqual(len(response.context['rows']), 2)
        self.assertEqual(response.context['summary']['projects'], 32)
        self.assertEqual(response.context['summary']['hours'], Decimal('31.00'))
        # Building rows takes one projects query, not a plan/time query for each project.
        with self.assertNumQueries(1):
            rows = [report_row(p, []) for p in project_report_queryset()]
        self.assertEqual(len(rows), 32)
