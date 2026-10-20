from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase,TransactionTestCase,override_settings,skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from . import freshbooks as fb, freshbooks_projects as projects, dropbox_filing as filing
from .dropbox_models import DropboxConnection,DropboxFolder,ProjectFolder,DropboxFiling,ProjectMonthSequence
from .freshbooks_models import FreshBooksConnection,FreshBooksClientLink,FreshBooksInvoice
from .freshbooks_project_models import FreshBooksProject,FreshBooksClientProvision
from .models import Submission,NotificationDelivery,WorkItem,QueueState
from .services import create_work,EditConflict
from .test_dropbox import remote_folder
from .tests import work_data

SETTINGS=dict(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False,
    INTAKE_LOCAL_DEVELOPMENT=True,FRESHBOOKS_ENABLED=True,FRESHBOOKS_CLIENT_ID='fictional-id',
    FRESHBOOKS_CLIENT_SECRET='fictional-secret',FRESHBOOKS_REDIRECT_URI='https://testserver/work-projects/billing/callback/',
    FRESHBOOKS_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())


class ProjectFixture:
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('project-owner','owner@example.invalid','test-only')
        QueueState.objects.get_or_create(pk=1)
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.connection=FreshBooksConnection.objects.create(account_id='TEST123',business_id=77,business_name='Test business',granted_scopes=fb.SCOPES.split())
        fb.store_tokens(self.connection,dict(access_token='fictional-token',refresh_token='fictional-refresh',expires_in=3600))
        DropboxConnection.objects.create(account_id='fictional-dropbox',root_id='id:root',enabled=True)
        self.folder=remote_folder()
        patcher=patch('workqueue.dropbox_provider.children',return_value=[self.folder]);patcher.start();self.addCleanup(patcher.stop)
        filing.refresh_index()
        self.remote_clients=[];self.remote_projects=[];self.posts=[]
        patcher=patch.object(fb,'api',side_effect=self.api);self.api_mock=patcher.start();self.addCleanup(patcher.stop)
        self.client.force_login(self.owner)

    def api(self,method,path,**kwargs):
        if method=='GET' and path.endswith('/users/clients'):
            params=kwargs.get('params',{})
            rows=self.remote_clients
            if 'search[note_like]' in params:
                rows=[row for row in rows if params['search[note_like]'] in row.get('note','')]
            return {'response':{'result':{'clients':rows,'pages':1}}}
        if method=='GET' and '/users/clients/' in path:
            return {'response':{'result':{'client':next(row for row in self.remote_clients if row['id']==int(path.rsplit('/',1)[1]))}}}
        if method=='GET' and path.endswith('/projects'):
            return {'projects':self.remote_projects,'meta':{'pages':1}}
        if method=='GET' and '/project/' in path:
            return {'project':next(row for row in self.remote_projects if row['id']==int(path.rsplit('/',1)[1]))}
        if method=='POST' and path.endswith('/users/clients'):
            self.posts.append((path,kwargs['data']))
            row={**kwargs['data']['client'],'id':7,'vis_state':0};self.remote_clients.append(row)
            return {'response':{'result':{'client':row}}}
        if method=='POST' and path.endswith('/project'):
            self.posts.append((path,kwargs['data']))
            row={**kwargs['data']['project'],'id':42,'active':True};self.remote_projects.append(row)
            return {'project':row}
        raise AssertionError((method,path))

    def existing_client(self):
        self.remote_clients=[dict(id=7,fname='Morgan',lname='Lee',email='morgan@example.com',organization='',vis_state=0)]
        FreshBooksClientLink.objects.create(client=self.item.client_contact.client,account_id='TEST123',customer_id=7,label='Morgan Lee')

    def preview(self,**billing):
        return filing.preview(item=self.item,mode='existing',folder_id=self.folder['id'],actor=self.owner,
            freshbooks={'action':'create','pricing':'fixed_price','amount':'2500.00',**billing})

    def approve(self,**billing):
        data,signed=self.preview(**billing)
        filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        return FreshBooksProject.objects.get(project=self.item.project)


@override_settings(**SETTINGS)
class FreshBooksProjectTests(ProjectFixture,TestCase):
    def test_preview_read_only_has_exact_client_price_and_destination(self):
        data,_=self.preview()
        self.assertEqual(data['freshbooks']['payload']['fixed_price'],'2500.00')
        self.assertEqual(data['freshbooks']['title'],self.folder['name'])
        self.assertEqual(data['freshbooks']['customer']['mode'],'new')
        self.assertFalse(FreshBooksProject.objects.exists());self.assertFalse(ProjectFolder.objects.exists())
        self.assertFalse(self.posts)

    def test_hourly_uses_project_rate_and_rejects_missing_or_invalid_amount(self):
        data,_=self.preview(pricing='hourly_rate',amount='125.50')
        self.assertEqual(data['freshbooks']['payload']['rate'],'125.50')
        self.assertEqual(data['freshbooks']['payload']['billing_method'],'project_rate')
        for amount in [None,'0','-1','NaN','Infinity','1.001']:
            with self.assertRaises(ValidationError): self.preview(amount=amount)

    def test_old_authorization_does_not_silently_gain_projects(self):
        self.connection.granted_scopes=[];self.connection.save()
        self.assertFalse(projects.ready())
        with self.assertRaisesMessage(ValidationError,'Reconnect FreshBooks'): self.preview()
        filing.preview(item=self.item,mode='existing',folder_id=self.folder['id'],actor=self.owner)

    def test_combined_approval_one_project_and_no_queue_or_email_side_effect(self):
        before=list(WorkItem.objects.values());data,signed=self.preview()
        filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertEqual(FreshBooksProject.objects.count(),1);self.assertEqual(DropboxFiling.objects.count(),1)
        self.assertEqual(before,list(WorkItem.objects.values()));self.assertFalse(self.posts)
        self.assertFalse(NotificationDelivery.objects.exists());self.assertFalse(FreshBooksInvoice.objects.exists())

    def test_changed_client_or_business_rolls_back_number_and_filing(self):
        data,signed=self.preview();client=self.item.client_contact.client;client.version+=1;client.save()
        with self.assertRaises(EditConflict): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertFalse(FreshBooksProject.objects.exists());self.assertFalse(DropboxFiling.objects.exists())
        self.assertFalse(ProjectFolder.objects.exists());self.assertFalse(ProjectMonthSequence.objects.exists())

    def test_shared_email_different_company_requires_explicit_billing_link(self):
        self.remote_clients=[dict(id=7,fname='Morgan',lname='Lee',email='morgan@example.com',organization='Separate Company',vis_state=0)]
        self.item.client_contact.client.kind='company';self.item.client_contact.client.save()
        with self.assertRaisesMessage(ValidationError,'possible client match'): self.preview()
        self.assertFalse(self.posts)

    def test_exact_client_match_reuses_without_client_post(self):
        self.existing_client();FreshBooksClientLink.objects.all().delete()
        job=self.approve();self.assertEqual(job.customer_id,7)
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'linked')
        self.assertEqual(len(self.posts),1);self.assertTrue(self.posts[0][0].endswith('/project'))

    def test_archived_match_and_ambiguous_matches_do_not_create_clients(self):
        self.remote_clients=[dict(id=7,fname='Morgan',lname='Lee',email='morgan@example.com',vis_state=1)]
        with self.assertRaises(ValidationError): self.preview()
        self.remote_clients[0]['vis_state']=0;self.remote_clients.append({**self.remote_clients[0],'id':8})
        with self.assertRaises(ValidationError): self.preview()

    def test_duplicate_project_title_requires_link(self):
        self.existing_client();self.remote_projects=[dict(id=42,title=self.folder['name'],client_id=7)]
        with self.assertRaisesMessage(ValidationError,'already exists'): self.preview()
        job=self.approve(action='link',remote_id=42)
        self.assertEqual(job.remote_id,42);self.assertEqual(job.state,'linked');self.assertFalse(self.posts)

    def test_same_number_created_after_preview_blocks_duplicate(self):
        self.existing_client();job=self.approve()
        self.remote_projects=[dict(id=42,title='261007 - Manually named project',client_id=7)]
        projects.process_one();job.refresh_from_db()
        self.assertEqual(job.state,'attention');self.assertFalse(self.posts)

    def test_link_checks_client_and_unique_website_project(self):
        self.existing_client();self.remote_projects=[dict(id=42,title=self.folder['name'],client_id=8)]
        with self.assertRaisesMessage(ValidationError,'different billing client'): self.preview(action='link',remote_id=42)

    def test_new_client_and_project_created_in_separate_committed_steps(self):
        job=self.approve();projects.process_one();job.refresh_from_db()
        self.assertEqual(job.state,'queued');self.assertEqual(job.customer_id,7)
        self.assertEqual(FreshBooksClientProvision.objects.get().state,'linked')
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'linked');self.assertEqual(job.remote_id,42)
        self.assertEqual(len(self.posts),2);projects.process_one();self.assertEqual(len(self.posts),2)
        self.assertNotIn('internal_notes',str(self.posts));self.assertFalse(NotificationDelivery.objects.exists())

    def test_timeout_project_write_never_reposted_and_reconciles(self):
        self.existing_client();job=self.approve();real_api=self.api
        def timeout(method,path,**kwargs):
            result=real_api(method,path,**kwargs)
            if method=='POST': raise fb.BillingError()
            return result
        self.api_mock.side_effect=timeout
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'unknown')
        projects.process_one();self.assertEqual(len(self.posts),1)
        with self.assertRaises(ValidationError): projects.recover(job.pk,self.owner,action='retry')
        self.api_mock.side_effect=real_api;projects.recover(job.pk,self.owner,action='check')
        job.refresh_from_db();self.assertEqual(job.state,'linked');self.assertEqual(len(self.posts),1)

    def test_billing_link_changed_after_approval_is_held(self):
        job=self.approve();self.existing_client()
        projects.process_one();job.refresh_from_db()
        self.assertEqual(job.state,'attention');self.assertFalse(self.posts)

    def test_timeout_client_write_never_reposted_and_reconciles(self):
        job=self.approve();real_api=self.api
        def timeout(method,path,**kwargs):
            result=real_api(method,path,**kwargs)
            if method=='POST': raise fb.BillingError()
            return result
        self.api_mock.side_effect=timeout;projects.process_one();job.refresh_from_db()
        self.assertEqual(job.state,'unknown');self.assertEqual(FreshBooksClientProvision.objects.get().state,'unknown')
        projects.process_one();self.assertEqual(len(self.posts),1)
        self.api_mock.side_effect=real_api;projects.recover(job.pk,self.owner,action='check')
        job.refresh_from_db();self.assertEqual(job.customer_id,7);self.assertEqual(job.state,'queued')

    def test_unmatched_unknown_stays_held_without_post(self):
        self.existing_client();job=self.approve();job.state='unknown';job.save()
        with self.assertRaises(ValidationError): projects.recover(job.pk,self.owner,action='check')
        job.refresh_from_db();self.assertEqual(job.state,'unknown');self.assertFalse(self.posts)

    def test_definite_rejection_can_retry_but_bad_response_is_unknown(self):
        self.existing_client();job=self.approve();real_api=self.api
        def rejected(method,path,**kwargs):
            if method=='POST': raise fb.BillingError(status=422)
            return real_api(method,path,**kwargs)
        self.api_mock.side_effect=rejected;projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'attention')
        projects.recover(job.pk,self.owner,action='retry');job.refresh_from_db();self.assertEqual(job.state,'queued')
        self.api_mock.side_effect=lambda method,path,**kwargs: {'project':{}} if method=='POST' else real_api(method,path,**kwargs)
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'unknown')

    def test_interrupted_work_is_held_not_replayed(self):
        self.existing_client();job=self.approve();job.state='working';job.attempted_at=timezone.now()-timedelta(minutes=11);job.save()
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'unknown');self.assertFalse(self.posts)

    def test_waits_for_folder_and_does_not_write_after_deleted_request(self):
        self.existing_client();job=self.approve();binding=ProjectFolder.objects.get();binding.folder=None;binding.save()
        projects.process_one();job.refresh_from_db();self.assertEqual(job.state,'queued');self.assertFalse(self.posts)
        self.item.deleted_at=timezone.now();self.item.save();projects.process_one();job.refresh_from_db()
        self.assertEqual(job.state,'attention');self.assertFalse(self.posts)

    def test_updates_reuse_existing_project_without_changing_pricing(self):
        self.existing_client();job=self.approve();projects.process_one()
        Submission.objects.create(work_item=self.item,idempotency_key='later-update')
        data,signed=self.preview(amount=None)
        self.assertEqual(data['freshbooks']['action'],'reuse')
        filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertEqual(FreshBooksProject.objects.count(),1);projects.process_one();self.assertEqual(len(self.posts),1)

    def test_can_add_freshbooks_to_already_filed_project(self):
        _,signed=filing.preview(item=self.item,mode='existing',folder_id=self.folder['id'],actor=self.owner)
        filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertFalse(filing.pending_submissions(self.item))
        job=self.approve();self.assertEqual(job.state,'queued');self.assertEqual(DropboxFiling.objects.count(),1)

    def test_read_only_staff_and_anonymous_cannot_approve_or_recover(self):
        from django.contrib.auth.models import Permission
        staff=get_user_model().objects.create_user('project-viewer',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(staff)
        response=self.client.post(reverse('workqueue:dropbox_review',args=[self.item.pk]),{'action':'freshbooks_retry','confirm':'yes'})
        self.assertEqual(response.status_code,403)
        self.client.logout();self.assertEqual(self.client.get(reverse('workqueue:dropbox_review',args=[self.item.pk])).status_code,302)

    def test_form_default_fixed_price_preview_and_recovery_render(self):
        response=self.client.get(reverse('workqueue:dropbox_review',args=[self.item.pk]))
        self.assertContains(response,'Fixed price');self.assertEqual(response.context['form'].initial['freshbooks_pricing'],'fixed_price')
        response=self.client.post(reverse('workqueue:dropbox_review',args=[self.item.pk]),dict(action='preview',mode='existing',folder=self.folder['id'],freshbooks_action='create',freshbooks_pricing='fixed_price',freshbooks_amount='2500'))
        self.assertEqual(response.status_code,200);self.assertContains(response,'Fixed price: 2500.00')
        job=self.approve();job.state='unknown';job.save()
        response=self.client.get(reverse('workqueue:dropbox_review',args=[self.item.pk]))
        self.assertContains(response,'Check FreshBooks');self.assertNotContains(response,'Retry rejected FreshBooks step')

    def test_paginated_lists_cannot_hide_duplicates_or_repeat_pages(self):
        self.api_mock.side_effect=[{'projects':[{'id':i} for i in range(1,101)],'meta':{'pages':2}}, {'projects':[{'id':101}],'meta':{'pages':2}}]
        self.assertEqual(len(projects.projects(77)),101)
        self.api_mock.side_effect=[{'projects':[{'id':1}],'meta':{'pages':2}},{'projects':[{'id':1}],'meta':{'pages':2}}]
        with self.assertRaisesMessage(ValidationError,'repeated'): projects.projects(77)

    def test_scope_changes_persist_and_refresh_without_scope_keeps_grant(self):
        self.connection.granted_scopes=[];self.connection.save()
        fb.store_tokens(self.connection,dict(access_token='new',refresh_token='newrefresh',expires_in=3600,scope=fb.SCOPES))
        fb.store_tokens(self.connection,dict(access_token='newer',refresh_token='newerrefresh',expires_in=3600))
        self.connection.refresh_from_db();self.assertTrue(projects.PROJECT_SCOPES.issubset(self.connection.granted_scopes))


@override_settings(**SETTINGS)
class FreshBooksProjectConcurrencyTests(ProjectFixture,TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update_skip_locked')
    def test_two_workers_never_post_the_same_project_twice(self):
        self.existing_client();job=self.approve();entered,release=Event(),Event();real_api=self.api
        def slow(method,path,**kwargs):
            if method=='POST':
                entered.set();release.wait(10)
            return real_api(method,path,**kwargs)
        self.api_mock.side_effect=slow
        def worker():
            close_old_connections()
            try:return projects.process_one()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(worker)
            self.assertTrue(entered.wait(10))
            self.assertFalse(pool.submit(worker).result(10))
            release.set();self.assertTrue(first.result(10))
        self.assertEqual(len(self.posts),1);job.refresh_from_db();self.assertEqual(job.state,'linked')
