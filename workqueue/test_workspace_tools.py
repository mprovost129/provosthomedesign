from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from .models import Client, ClientContact, WorkItem, WorkProject, Status, NotificationDelivery
from .pm_models import ProjectPlan, ProjectTask, Proposal, ProposalRevision
from .freshbooks_models import FreshBooksInvoice
from .invoice_history_models import HistoricalInvoice, InvoiceCSVImport
from .dropbox_models import ProjectFolder
from .time_models import TimeEntry
from .services import create_work
from .staff_search import saved_records
from .workload import workload_board
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class WorkspaceToolsTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('workspace-owner','owner@example.invalid','test')
        self.client.force_login(self.owner)
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.project=self.item.project
        self.person=self.item.client_contact.client
        self.search=reverse('workqueue:staff_search')
        self.workload=reverse('workqueue:workload')
        self.start=date(2026,10,19)

    def test_search_and_workload_are_staff_only_and_require_queue_permission(self):
        for url in [self.search,self.workload]:
            self.client.logout();self.assertIn('/staff/login/',self.client.get(url).url)
            staff=get_user_model().objects.create_user('staff-'+url.split('/')[1],is_staff=True)
            self.client.force_login(staff);self.assertEqual(self.client.get(url).status_code,403)
            staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
            self.assertEqual(self.client.get(url).status_code,200)

    def test_shared_search_matches_project_number_request_id_and_contact_email(self):
        ProjectFolder.objects.create(project=self.project,number='261099',intended_name='Preview')
        for query,kind in [('261099','projects'),(self.item.reference,'requests'),('morgan@example.com','clients')]:
            response=self.client.get(self.search,{'q':query})
            group=next(group for group in response.context['groups'] if group['key']==kind)
            self.assertEqual(group['count'],1)
            self.assertContains(response,escape(group['rows'][0]['url']),html=False)
        self.assertContains(self.client.get(self.search,{'q':'Park addition'}),'Park Street addition')

    def test_archived_contacts_merged_clients_and_deleted_requests_are_excluded(self):
        contact=ClientContact.objects.create(client=self.person,email='archived@example.invalid',archived_at=timezone.now())
        merged=Client.objects.create(name='Merged preview',normalized_name='merged preview',merged_into=self.person)
        self.assertEqual(dict((key,qs.count()) for key,_,qs in saved_records(['archived@example.invalid']))['clients'],0)
        self.assertEqual(dict((key,qs.count()) for key,_,qs in saved_records(['Merged']))['clients'],0)
        WorkItem.all_objects.filter(pk=self.item.pk).update(deleted_at=timezone.now())
        response=self.client.get(self.search,{'q':self.item.reference,'type':'requests'})
        self.assertEqual(response.context['result_count'],0)

    def test_search_is_bounded_paginated_and_escapes_untrusted_text(self):
        for index in range(35):Client.objects.create(name=f'Alpha {index}',normalized_name=f'alpha {index}')
        response=self.client.get(self.search,{'q':'Alpha','type':'clients','page':2})
        self.assertEqual(len(response.context['groups'][0]['rows']),5)
        self.assertEqual(response.context['result_count'],35)
        self.assertContains(self.client.get(self.search,{'q':'Alpha'}),'View all 35 clients')
        self.assertEqual(self.client.get(self.search,{'q':'a'}).context['groups'],[])
        response=self.client.get(self.search,{'q':'<script>alert(1)</script>','type':'invalid'})
        self.assertEqual(response.context['kind'],'all')
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertIn('no-store',response['Cache-Control'])

    def test_search_invoice_history_prefers_same_account_connected_number(self):
        proposal=Proposal.objects.create(project=self.project,client=self.person,number='EST-SEARCH',title='Test proposal')
        revision=ProposalRevision.objects.create(proposal=proposal,sequence=1,snapshot={},recipient='owner@example.invalid')
        FreshBooksInvoice.objects.create(proposal=proposal,revision=revision,account_id='TEST',customer_id=1,marker='SEARCH',payload={},invoice_number='INV-SEARCH',state='linked')
        batch=InvoiceCSVImport.objects.create(actor=self.owner,account_id='TEST',business_name='Test',filename='test.csv',source_hash='test',invoices=[])
        for account in ['TEST','OTHER']:
            HistoricalInvoice.objects.create(batch=batch,account_id=account,number='INV-SEARCH',exported_client='Test',issued=self.start,exported_status='Paid',currency='USD',line_total=1,lines=[],fingerprint=account)
        response=self.client.get(self.search,{'q':'INV-SEARCH'})
        counts={group['key']:group['count'] for group in response.context['groups']}
        self.assertEqual(counts['invoices'],1);self.assertEqual(counts['history'],1)
        response=self.client.get(self.search,{'q':'EST-SEARCH','type':'proposals'})
        self.assertEqual(response.context['result_count'],1)

    def test_planning_counts_one_project_not_each_update_and_uses_exact_time(self):
        ProjectPlan.objects.create(project=self.project,estimated_hours=10,target_date=self.start+timedelta(days=10))
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        WorkItem.objects.filter(pk=self.item.pk).update(committed_due_date=self.start+timedelta(days=2))
        TimeEntry.objects.create(project=self.project,actor=self.owner,duration_seconds=7200,billable=False)
        TimeEntry.objects.create(project=self.project,actor=self.owner,duration_seconds=9999,deleted_at=timezone.now())
        TimeEntry.objects.create(project=self.project,actor=self.owner,source='timer',started_at=timezone.now())
        board=workload_board(self.start,self.start)
        self.assertEqual(board['weeks'][0]['count'],1)
        self.assertEqual(board['weeks'][0]['hours'],Decimal('8.00'))
        self.assertEqual(board['weeks'][1]['count'],0)
        self.assertEqual(board['weeks'][0]['rows'][0]['project'].active_count,2)

    def test_planning_distinguishes_missing_estimate_zero_and_overrun(self):
        board=workload_board(self.start,self.start)
        self.assertEqual(board['unscheduled']['unknown'],1)
        plan=ProjectPlan.objects.create(project=self.project,estimated_hours=0)
        TimeEntry.objects.create(project=self.project,actor=self.owner,duration_seconds=1)
        board=workload_board(self.start,self.start)
        self.assertEqual(board['unscheduled']['unknown'],0)
        self.assertEqual(board['unscheduled']['hours'],0)
        self.assertTrue(board['unscheduled']['rows'][0]['over_budget'])

    def test_closed_deleted_and_unlinked_work_do_not_disappear_into_totals(self):
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        board=workload_board(self.start,self.start)
        self.assertEqual(board['unscheduled']['count'],0)
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.NEW,project=None)
        self.assertEqual(workload_board(self.start,self.start)['unlinked_count'],1)
        WorkItem.all_objects.filter(pk=self.item.pk).update(deleted_at=timezone.now())
        self.assertEqual(workload_board(self.start,self.start)['unlinked_count'],0)

    def test_dates_tasks_and_week_navigation(self):
        WorkItem.objects.filter(pk=self.item.pk).update(scheduled_start=self.start+timedelta(days=8))
        ProjectTask.objects.create(project=self.project,title='Preview task',due_date=self.start+timedelta(days=8))
        board=workload_board(self.start,self.start)
        self.assertEqual(board['weeks'][1]['count'],1);self.assertEqual(board['weeks'][1]['tasks'],1)
        self.assertEqual(board['weeks'][1]['rows'][0]['date_label'],'Starts')
        for value in ['garbage','0001-01-01','9999-12-31']:
            self.assertEqual(self.client.get(self.workload,{'week':value}).status_code,200)
        response=self.client.get(self.workload,{'week':'2026-10-21'})
        self.assertEqual(response.context['week_start'],self.start)
        self.assertContains(response,'Running timers are excluded')

    def test_read_only_pages_do_not_change_positions_or_contact_providers(self):
        before=list(WorkItem.objects.values_list('pk','queue_order','version'))
        notices=NotificationDelivery.objects.count()
        with patch('workqueue.freshbooks.api',side_effect=AssertionError('No API on GET')),CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.search,{'q':'Park'}).status_code,200)
            self.assertEqual(self.client.get(self.workload).status_code,200)
        self.assertFalse(any(query['sql'].lstrip().split()[0] in {'INSERT','UPDATE','DELETE'} for query in queries))
        self.assertEqual(before,list(WorkItem.objects.values_list('pk','queue_order','version')))
        self.assertEqual(notices,NotificationDelivery.objects.count())

    def test_queue_cards_keep_one_status_form_and_working_action_targets(self):
        response=self.client.get(reverse('workqueue:queue'))
        self.assertContains(response,'class="queue-list" role="table"')
        self.assertContains(response,'data-label="Status"')
        self.assertContains(response,f'id="status-{self.item.pk}"',count=1)
        self.assertContains(response,f'data-edit-target="manage-{self.item.pk}"')
        self.assertContains(response,'queue-row-actions')
