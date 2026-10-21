from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .dashboard_views import recent_invoices
from .freshbooks_models import FreshBooksInvoice
from .invoice_history_models import HistoricalInvoice, HistoricalInvoiceBalance, InvoiceCSVImport
from .models import Appointment, Client, ClientFollowUp, NotificationDelivery, Status, WorkItem, WorkProject
from .pm_models import ProjectTask, Proposal, ProposalRevision
from .services import create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=True)
class DashboardTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('dashboard-owner', 'owner@example.invalid', 'test')
        self.client.force_login(self.owner)
        self.url = reverse('workqueue:dashboard')
        self.now = timezone.now()
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.person = self.item.client_contact.client
        self.index = 0

    def invoice(self, **changes):
        self.index += 1
        proposal = Proposal.objects.create(project=self.item.project, client=self.person,
            number=f'EST-DASH-{self.index}', title='Design services', has_draft=False)
        revision = ProposalRevision.objects.create(proposal=proposal, sequence=1, state='accepted',
            snapshot={}, recipient='client@example.invalid')
        proposal.current_revision = revision
        proposal.save()
        return FreshBooksInvoice.objects.create(**{
            **dict(proposal=proposal, revision=revision, account_id='TEST', customer_id=7,
                marker=proposal.number, payload={'secret': 'PRIVATE-INVOICE-PAYLOAD'}, state='linked',
                invoice_id=self.index, invoice_number=f'INV-{self.index}', billing_status='sent', currency='USD',
                amount=100, paid=0, outstanding=100, last_sync_at=self.now), **changes})

    def historical(self, number='HIST-1', **changes):
        batch = InvoiceCSVImport.objects.create(actor=self.owner, account_id='TEST', business_name='Test',
            filename='test.csv', source_hash='a' * 64, invoices=[], applied_at=self.now)
        return HistoricalInvoice.objects.create(**{**dict(batch=batch, account_id='TEST', number=number,
            exported_client='Imported client', issued=timezone.localdate(), exported_status='Paid',
            currency='USD', line_total=100, lines=[{'private': 'PRIVATE-IMPORTED-LINES'}], fingerprint='b' * 64), **changes})

    def appointment(self, **changes):
        start = self.now + timedelta(days=1)
        return Appointment.objects.create(**{**dict(email='meeting@example.invalid', full_name='Appointment client',
            phone='555-0100', purpose='Project review', starts_at=start, ends_at=start + timedelta(minutes=30),
            reserved_until=start + timedelta(hours=1), local_date=timezone.localdate(start), calendar_id='preview',
            state='confirmed', idempotency_key='dashboard-meeting'), **changes})

    def test_public_user_cannot_read_business_data(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)
        self.assertNotIn(self.person.name, response.content.decode())

    def test_logged_in_client_cannot_read_business_data(self):
        user = get_user_model().objects.create_user('dashboard-client')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_requires_existing_queue_permission(self):
        user = get_user_model().objects.create_user('dashboard-staff', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    @override_settings(WORK_QUEUE_ENABLED=False)
    def test_disabled_queue_hides_dashboard(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_dashboard_is_read_only_and_never_cached(self):
        self.invoice()
        self.historical()
        before = NotificationDelivery.objects.count()
        with patch('workqueue.freshbooks.api', side_effect=AssertionError('No provider calls on GET')), \
             patch('workqueue.dropbox_provider.api', side_effect=AssertionError('No Dropbox calls on GET'), create=True), \
             CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex, nofollow', response.content.decode())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.assertEqual(NotificationDelivery.objects.count(), before)
        self.assertEqual(self.client.post(self.url, {'status': 'completed'}).status_code, 405)

    def test_counts_do_not_multiply_projects_or_clients_for_updates(self):
        create_work(data=work_data(kind='update', project=self.item.project), actor=self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_total'], 2)
        self.assertEqual(response.context['project_total'], 1)
        self.assertEqual(response.context['client_total'], 1)
        self.assertEqual(response.context['recent_projects'][0].active_count, 2)
        self.assertEqual(response.context['recent_clients'][0].active_count, 2)

    def test_closed_deleted_and_merged_records_are_not_active(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        gone = create_work(data=work_data(project_name='REMOVED-PROJECT'), actor=self.owner)[0]
        WorkItem.objects.filter(pk=gone.pk).update(deleted_at=self.now)
        merged = Client.objects.create(name='MERGED-PROFILE', normalized_name='merged', merged_into=self.person)
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_total'], 0)
        self.assertEqual(response.context['project_total'], 0)
        self.assertNotIn(gone, response.context['recent_requests'])
        self.assertNotIn(merged, response.context['recent_clients'])
        self.assertNotContains(response, 'REMOVED-PROJECT')
        self.assertNotContains(response, 'MERGED-PROFILE')

    def test_recent_projects_and_clients_follow_work_activity(self):
        old = self.now - timedelta(days=100)
        WorkProject.objects.filter(pk=self.item.project_id).update(created_at=old)
        Client.objects.filter(pk=self.person.pk).update(created_at=old, updated_at=old)
        WorkItem.objects.filter(pk=self.item.pk).update(updated_at=self.now)
        response = self.client.get(self.url)
        self.assertEqual(response.context['recent_projects'][0].last_activity, self.now)
        self.assertEqual(response.context['recent_clients'][0].last_activity, self.now)

    def test_attention_links_use_the_existing_filters_and_due_boundaries(self):
        today = timezone.localdate()
        WorkItem.objects.filter(pk=self.item.pk).update(committed_due_date=today - timedelta(days=1), followup_date=today)
        ClientFollowUp.objects.create(client=self.person, title='Call client', due_date=today)
        ProjectTask.objects.create(project=self.item.project, title='Due today', due_date=today)
        ProjectTask.objects.create(project=self.item.project, title='Late task', due_date=today - timedelta(days=1))
        ProjectTask.objects.create(project=self.item.project, title='Already done', due_date=today, completed_at=self.now)
        response = self.client.get(self.url)
        cards = {row['label']: row for row in response.context['attention']}
        for label in ['Overdue', 'Follow-ups due', 'Client follow-ups due', 'Project tasks due today', 'Overdue project tasks']:
            self.assertEqual(cards[label]['count'], 1)
        self.assertIn('view=today', cards['Project tasks due today']['url'])
        self.assertIn('view=overdue', cards['Overdue project tasks']['url'])
        self.assertIn('attention=due', cards['Client follow-ups due']['url'])

    def test_tasks_on_closed_projects_do_not_need_attention(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        ProjectTask.objects.create(project=self.item.project, title='Old task', due_date=timezone.localdate())
        self.assertFalse(any('project tasks' in row['label'] for row in self.client.get(self.url).context['attention']))

    def test_appointments_include_current_and_future_but_not_canceled(self):
        meeting = self.appointment()
        canceled = self.appointment(idempotency_key='canceled', state='canceled', full_name='CANCELED-MEETING')
        self.appointment(idempotency_key='cancel-request', cancel_requested=True, full_name='CANCEL-REQUESTED')
        self.appointment(idempotency_key='past', starts_at=self.now-timedelta(hours=2), ends_at=self.now-timedelta(hours=1),
            reserved_until=self.now-timedelta(minutes=30), full_name='PAST-MEETING')
        response = self.client.get(self.url)
        self.assertEqual(list(response.context['appointments']), [meeting])
        self.assertNotContains(response, canceled.full_name)
        self.assertNotContains(response, 'CANCEL-REQUESTED')
        self.assertNotContains(response, 'PAST-MEETING')

    @override_settings(BOOKING_ENABLED=False)
    def test_booking_disabled_is_explained(self):
        self.appointment()
        response = self.client.get(self.url)
        self.assertContains(response, 'Online appointment booking is turned off.')
        self.assertNotContains(response, 'Appointment client')

    def test_recent_invoice_history_included_and_linked_duplicate_removed(self):
        invoice = self.invoice(invoice_number=' INV-MATCH ')
        duplicate = self.historical('inv-match')
        separate = self.historical('IMPORT-ONLY')
        rows = recent_invoices(self.now)
        self.assertEqual(len(rows), 2)
        self.assertNotIn(str(duplicate.pk), [r['key'] for r in rows])
        self.assertIn(str(separate.pk), [r['key'] for r in rows])
        self.assertIn(str(invoice.pk), [r['key'] for r in rows])

    def test_invoice_deduplication_by_provider_id_is_scoped_to_business(self):
        invoice = self.invoice()
        match = self.historical('RENAMED')
        HistoricalInvoiceBalance.objects.create(invoice=match, provider_invoice_id=invoice.invoice_id)
        other = self.historical('OTHER-ACCOUNT', account_id='OTHER')
        HistoricalInvoiceBalance.objects.create(invoice=other, provider_invoice_id=invoice.invoice_id)
        keys = [r['key'] for r in recent_invoices(self.now)]
        self.assertNotIn(str(match.pk), keys)
        self.assertIn(str(other.pk), keys)

    def test_removed_history_and_raw_payloads_are_not_shown(self):
        self.invoice()
        self.historical()
        self.historical('REMOVED-INVOICE', removed_at=self.now)
        response = self.client.get(self.url)
        self.assertNotContains(response, 'REMOVED-INVOICE')
        self.assertNotContains(response, 'PRIVATE-INVOICE-PAYLOAD')
        self.assertNotContains(response, 'PRIVATE-IMPORTED-LINES')
        self.assertContains(response, 'Saved information only')

    def test_stale_invoice_and_unverified_history_are_labeled(self):
        self.invoice(last_sync_at=self.now-timedelta(days=2))
        self.historical()
        response = self.client.get(self.url)
        self.assertTrue(all(r['stale'] for r in response.context['recent_invoices']))
        self.assertContains(response, 'Needs refresh or review')
        self.assertContains(response, 'Not checked with FreshBooks')

    def test_recent_lists_are_bounded_and_empty_dashboard_renders(self):
        for index in range(8):
            self.historical(f'HIST-{index + 2}')
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['recent_invoices']), 6)
        WorkItem.objects.all().update(deleted_at=self.now)
        Client.objects.all().update(merged_into=self.person)  # fixture only: no current profiles
        HistoricalInvoice.objects.all().update(removed_at=self.now)
        response = self.client.get(self.url)
        self.assertContains(response, 'New submissions will appear here.')
        self.assertContains(response, 'No connected or imported invoices yet.')
        self.assertEqual(response.context['active_total'], 0)

    def test_content_is_escaped_and_query_count_is_bounded(self):
        self.historical(exported_client='<script>private()</script>')
        response = self.client.get(self.url)
        self.assertContains(response, '&lt;script&gt;private()&lt;/script&gt;')
        self.assertNotContains(response, '<script>private()</script>')
        for index in range(8):
            self.invoice(invoice_number=f'CHECK-{index}')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 45)

    def test_dashboard_navigation_respects_staff_permissions(self):
        response = self.client.get(reverse('workqueue:clients'))
        self.assertContains(response, f'href="{self.url}"')
        self.client.logout()
        response = self.client.get(reverse('workqueue:tracking'))
        self.assertNotContains(response, f'href="{self.url}"')
