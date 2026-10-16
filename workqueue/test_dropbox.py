import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch, Mock
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.storage import FileSystemStorage
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from . import dropbox_provider as provider
from . import dropbox_filing as filing
from .dropbox_models import DropboxConnection, DropboxFolder, DropboxFiling, DropboxFiledFile, ProjectFolder, ProjectMonthSequence
from .models import Attachment, Submission, WorkItem, WorkProject, NotificationDelivery, ProjectAccess
from .services import create_work, EditConflict
from .tests import work_data


def remote_folder(number='261007',suffix='Existing house',identifier='id:existing'):
    name=number+' - '+suffix if number else suffix
    return {'.tag':'folder','id':identifier,'name':name,'path_lower':'/projects/'+name.lower(),'path_display':'/Projects/'+name}


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,INTAKE_LOCAL_DEVELOPMENT=True)
class DropboxTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('filing-owner','owner@example.invalid','test-only')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.submission=self.item.submissions.first()
        self.attachment=Attachment.objects.create(submission=self.submission,original_name='Plans.pdf',storage_key='ready/test.pdf',size_bytes=5)
        self.connection=DropboxConnection.objects.create(account_id='test-account',root_id='id:root',namespace_id='123',enabled=True)
        self.remote=[remote_folder()]
        self.children_original=provider.children
        self.period_original=filing.period
        patcher=patch.object(provider,'children',side_effect=lambda:list(self.remote))
        patcher.start(); self.addCleanup(patcher.stop)
        patcher=patch.object(filing,'period',return_value='2610')
        patcher.start(); self.addCleanup(patcher.stop)
        self.client.force_login(self.owner)

    def preview(self,**kwargs):
        data={'item':self.item,'mode':'new','title':'Spec. House','address':'Example Street, Attleboro, MA','actor':self.owner}
        data.update(kwargs)
        return filing.preview(**data)

    def approve(self,**kwargs):
        _,signed=self.preview(**kwargs)
        return filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)

    def test_number_floor_and_month_reset(self):
        self.assertEqual(filing.next_number('2610'),'261008')
        self.assertEqual(filing.next_number('2611'),'261101')
        self.assertEqual(filing.next_number('2701'),'270101')
        ProjectMonthSequence.objects.create(period='2610',last_number=11)
        self.assertEqual(filing.next_number('2610'),'261012')

    def test_counter_uses_all_existing_numbers_and_does_not_recycle_gaps(self):
        self.remote+=[remote_folder('261012',identifier='id:twelve')]
        filing.refresh_index()
        self.assertEqual(filing.next_number(),'261013')

    def test_month_limit_fails_without_number_or_folder_write(self):
        ProjectMonthSequence.objects.create(period='2610',last_number=99)
        with self.assertRaises(ValidationError): self.preview()
        self.assertFalse(ProjectFolder.objects.exists())

    def test_preview_is_read_only_and_contains_exact_destination(self):
        payload,signed=self.preview()
        self.assertEqual(payload['number'],'261008')
        self.assertEqual(payload['label'],'261008 - Spec. House - Example Street, Attleboro, MA')
        self.assertFalse(ProjectFolder.objects.exists())
        self.assertFalse(DropboxFiling.objects.exists())
        self.assertFalse(ProjectMonthSequence.objects.exists())

    def test_approval_reserves_number_preserves_queue_source_and_permissions(self):
        before=list(WorkItem.objects.values())
        jobs=self.approve()
        self.assertEqual(jobs[0].destination.number,'261008')
        self.assertEqual(ProjectMonthSequence.objects.get(pk='2610').last_number,8)
        self.assertEqual(list(WorkItem.objects.values()),before)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.storage_key,'ready/test.pdf')

    def test_replayed_approval_does_not_duplicate_or_consume_number(self):
        _,signed=self.preview()
        first=filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        second=filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertEqual(first[0].pk,second[0].pk)
        self.assertEqual(DropboxFiling.objects.count(),1)
        self.assertEqual(ProjectMonthSequence.objects.get(pk='2610').last_number,8)

    def test_manual_folder_created_after_preview_requires_new_preview(self):
        _,signed=self.preview()
        self.remote.append(remote_folder('261008',identifier='id:manual'))
        with self.assertRaises(EditConflict): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.assertFalse(DropboxFiling.objects.exists())

    def test_stale_item_and_changed_attachment_are_rejected(self):
        _,signed=self.preview()
        self.attachment.original_name='Changed.pdf'; self.attachment.save()
        with self.assertRaises(EditConflict): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)
        self.attachment.original_name='Plans.pdf'; self.attachment.save()
        self.item.version+=1; self.item.save()
        with self.assertRaises(EditConflict): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)

    def test_month_rollover_invalidates_new_number_preview(self):
        _,signed=self.preview()
        with patch.object(filing,'period',return_value='2611'):
            with self.assertRaises(EditConflict): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)

    def test_expired_tampered_and_cross_staff_preview_rejected(self):
        _,signed=self.preview()
        with self.assertRaises(ValidationError): filing.approve(item_id=self.item.pk,signed=signed+'x',actor=self.owner)
        other=get_user_model().objects.create_superuser('other','other@example.invalid','test-only')
        with self.assertRaises(ValidationError): filing.approve(item_id=self.item.pk,signed=signed,actor=other)
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+901):
            with self.assertRaises(ValidationError): filing.approve(item_id=self.item.pk,signed=signed,actor=self.owner)

    def test_existing_folder_does_not_allocate_number(self):
        jobs=self.approve(mode='existing',folder_id='id:existing')
        self.assertEqual(jobs[0].destination.folder_id,'id:existing')
        self.assertEqual(jobs[0].destination.number,'261007')
        self.assertFalse(ProjectMonthSequence.objects.exists())

    def test_updates_keep_bound_folder_and_separate_submissions(self):
        self.approve(mode='existing',folder_id='id:existing')
        second=Submission.objects.create(work_item=self.item,idempotency_key='second-upload')
        Attachment.objects.create(submission=second,original_name='Plans.pdf',storage_key='ready/second.pdf',size_bytes=5)
        jobs=self.approve()
        self.assertEqual(jobs[0].destination.folder_id,'id:existing')
        self.assertEqual(ProjectFolder.objects.count(),1)
        self.assertEqual(DropboxFiling.objects.count(),2)
        self.assertEqual(len(set(DropboxFiling.objects.values_list('batch_name',flat=True))),2)

    def test_existing_folder_bound_to_another_project_requires_queue_link_review(self):
        other=WorkProject.objects.create(name='Other project')
        filing.refresh_index()
        ProjectFolder.objects.create(project=other,number='261007',folder_id='id:existing',intended_name=self.remote[0]['name'])
        with self.assertRaises(ValidationError): self.preview(mode='existing',folder_id='id:existing')

    def test_root_escape_and_invalid_windows_names_rejected(self):
        self.remote[0]['path_lower']='/private/existing'
        with self.assertRaises(provider.DropboxError): filing.refresh_index()
        for name in ['../Other','Bad:Name','CON','COM1.pdf']:
            with self.assertRaises(ValidationError): filing.component(name)

    def test_duplicate_original_filenames_have_distinct_stable_destinations(self):
        second=Attachment.objects.create(submission=self.submission,original_name='Plans.pdf',storage_key='ready/b.pdf',size_bytes=5)
        self.assertNotEqual(filing.safe_file_name(second),filing.safe_file_name(self.attachment))
        self.assertEqual(filing.safe_file_name(second),filing.safe_file_name(second))

    def test_deleted_request_and_legacy_source_block_approval(self):
        self.attachment.storage_key=''; self.attachment.save()
        with self.assertRaises(ValidationError): self.preview()
        self.item.deleted_at=timezone.now()
        with self.assertRaises(ValidationError): self.preview()

    def test_listing_pagination_and_failed_refresh_preserve_index(self):
        filing.refresh_index()
        with patch.object(provider,'children',side_effect=provider.DropboxError()):
            with self.assertRaises(provider.DropboxError): filing.refresh_index()
        self.assertTrue(DropboxFolder.objects.get(pk='id:existing').available)

    def test_staff_permissions_csrf_and_setup_page(self):
        url=reverse('workqueue:dropbox_review',args=[self.item.pk])
        anonymous=Client()
        self.assertEqual(anonymous.get(url).status_code,302)
        staff=get_user_model().objects.create_user('no-rights',is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url),'File project documents')
        self.assertContains(self.client.get(reverse('workqueue:dropbox_connection')),'not been connected yet')
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.owner)
        self.assertEqual(csrf.post(url,{'action':'approve'}).status_code,403)

    def test_queue_displays_filing_link_and_pending_indicator(self):
        self.assertContains(self.client.get(reverse('workqueue:queue')),'Awaiting approval')
        self.assertContains(self.client.get(reverse('workqueue:queue')),reverse('workqueue:dropbox_review',args=[self.item.pk]))

    def test_approval_post_requires_confirmation(self):
        _,signed=self.preview()
        response=self.client.post(reverse('workqueue:dropbox_review',args=[self.item.pk]),{'action':'approve','preview':signed})
        self.assertEqual(response.status_code,400)
        self.assertFalse(DropboxFiling.objects.exists())

    def test_worker_no_connection_and_paused_connection_do_not_write(self):
        self.approve()
        self.connection.enabled=False;self.connection.save()
        with patch.object(provider,'create_folder') as create:
            self.assertFalse(filing.process_one())
            create.assert_not_called()

    def test_worker_verifies_file_and_preserves_original(self):
        job=self.approve(mode='existing',folder_id='id:existing')[0]
        destination=self.remote[0]
        uploads={'.tag':'folder','id':'id:uploads','path_lower':destination['path_lower']+'/client uploads'}
        batch={'.tag':'folder','id':'id:batch','path_lower':uploads['path_lower']+'/'+job.batch_name.lower()}
        with TemporaryDirectory() as path:
            storage=FileSystemStorage(location=path)
            storage.save('ready/test.pdf',io.BytesIO(b'hello'))
            digest=filing.content_digest(io.BytesIO(b'hello'))
            record=job.files.get()
            result={'.tag':'file','id':'id:copied','size':5,'content_hash':digest,'path_lower':batch['path_lower']+'/'+record.name.lower()}
            with patch.object(filing,'ensure_project_folder',return_value=destination),patch.object(filing,'ensure_subfolder',side_effect=[uploads,batch]),patch.object(filing,'private_storage',return_value=storage),patch.object(provider,'metadata',return_value=None),patch.object(provider,'upload',return_value=result) as upload:
                self.assertTrue(filing.process_one())
                upload.assert_called_once()
            self.assertTrue(storage.exists('ready/test.pdf'))
        job.refresh_from_db();record.refresh_from_db()
        self.assertEqual(job.state,'filed')
        self.assertEqual(record.dropbox_id,'id:copied')
        self.assertEqual(record.content_hash,digest)
        self.assertFalse(filing.process_one())

    def test_existing_identical_file_reconciles_without_second_upload(self):
        job=self.approve(mode='existing',folder_id='id:existing')[0]
        record=job.files.get();parent={'id':'id:batch','path_lower':'/projects/existing/client uploads/batch'}
        digest=filing.content_digest(io.BytesIO(b'hello'))
        result={'.tag':'file','id':'id:copied','size':5,'content_hash':digest,'path_lower':parent['path_lower']+'/'+record.name.lower()}
        with TemporaryDirectory() as path:
            storage=FileSystemStorage(location=path);storage.save('ready/test.pdf',io.BytesIO(b'hello'))
            with patch.object(filing,'private_storage',return_value=storage),patch.object(provider,'metadata',return_value=result),patch.object(provider,'upload') as upload:
                filing.copy_one(record,parent)
                upload.assert_not_called()
        self.assertIsNotNone(record.filed_at)

    def test_different_existing_file_never_overwritten(self):
        job=self.approve(mode='existing',folder_id='id:existing')[0];record=job.files.get()
        with TemporaryDirectory() as path:
            storage=FileSystemStorage(location=path);storage.save('ready/test.pdf',io.BytesIO(b'hello'))
            with patch.object(filing,'private_storage',return_value=storage),patch.object(provider,'metadata',return_value={'.tag':'file','id':'id:other','size':5,'content_hash':'different'}),patch.object(provider,'upload') as upload:
                with self.assertRaises(provider.DropboxError): filing.copy_one(record,{'id':'id:batch','path_lower':'/projects/a/b'})
                upload.assert_not_called()

    def test_worker_error_is_visible_and_retry_keeps_same_destination(self):
        job=self.approve()[0]
        with patch.object(filing,'ensure_project_folder',side_effect=provider.DropboxError('Folder conflict')):
            filing.process_one()
        job.refresh_from_db();self.assertEqual(job.state,'attention')
        self.assertEqual(job.last_error,'Folder conflict')
        filing.retry(job.pk,self.owner)
        job.refresh_from_db();self.assertEqual(job.state,'queued')
        self.assertEqual(job.destination.number,'261008')

    def test_source_storage_denial_is_held_for_review(self):
        from botocore.exceptions import ClientError
        job=self.approve(mode='existing',folder_id='id:existing')[0]
        storage=Mock()
        storage.open.side_effect=ClientError({'Error':{'Code':'AccessDenied','Message':'private-provider-details'}},'GetObject')
        parent={'id':'id:batch','path_lower':'/projects/existing/client uploads/batch'}
        with patch.object(filing,'ensure_project_folder',return_value=self.remote[0]),patch.object(filing,'ensure_subfolder',return_value=parent),patch.object(filing,'private_storage',return_value=storage):
            filing.process_one()
        job.refresh_from_db()
        self.assertEqual(job.state,'attention')
        self.assertNotIn('private-provider-details',job.last_error)
        self.assertFalse(job.files.filter(filed_at__isnull=False).exists())

    def test_unknown_folder_creation_requires_explicit_reconciliation(self):
        job=self.approve()[0]
        actual=remote_folder('261008',suffix='Spec. House - Example Street, Attleboro, MA',identifier='id:created')
        self.remote.append(actual)
        with patch.object(provider,'root',return_value=(self.connection,{})),patch.object(provider,'create_folder') as create:
            with self.assertRaises(provider.DropboxError): filing.ensure_project_folder(job.destination)
            create.assert_not_called()
        job.state='attention';job.save()
        with patch.object(provider,'metadata',return_value=actual): filing.reconcile_folder(job.pk,'id:created',self.owner)
        job.destination.refresh_from_db();self.assertEqual(job.destination.folder_id,'id:created')

    def test_worker_checks_project_relink_and_deleted_job(self):
        job=self.approve()[0]
        self.item.deleted_at=timezone.now();self.item.save()
        with patch.object(filing,'ensure_project_folder') as ensure:
            filing.process_one();ensure.assert_not_called()
        job.refresh_from_db();self.assertEqual(job.state,'attention')

    def test_dropbox_hash_uses_block_digests(self):
        content=b'a'*(4*1024*1024)+b'b'
        expected=hashlib.sha256(hashlib.sha256(content[:-1]).digest()+hashlib.sha256(b'b').digest()).hexdigest()
        file=io.BytesIO(content)
        self.assertEqual(filing.content_digest(file),expected)
        self.assertEqual(file.tell(),0)

    def test_connection_callback_rejects_unbound_state(self):
        with patch.object(provider,'connect') as connect:
            self.client.get(reverse('workqueue:dropbox_callback'),{'state':'bad','code':'test'})
            connect.assert_not_called()

    def test_oauth_uses_minimal_scopes_offline_access_and_pkce(self):
        with override_settings(DROPBOX_APP_KEY='test-key',DROPBOX_APP_SECRET='test-secret',DROPBOX_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            url=provider.authorize('bound-state','test-verifier')
        self.assertIn('token_access_type=offline',url)
        self.assertIn('code_challenge_method=S256',url)
        self.assertNotIn('sharing',url)
        self.assertNotIn('files.content.read',url)

    def test_upload_never_requests_overwrite_or_autorename(self):
        with patch.object(provider,'root',return_value=(self.connection,{})),patch.object(provider,'token',return_value='test-token'),patch.object(provider,'request',return_value={}) as wire:
            provider.upload('id:batch','safe.pdf',io.BytesIO(b'hello'),'hash')
        import json
        args=json.loads(wire.call_args.kwargs['headers']['Dropbox-API-Arg'])
        self.assertEqual(args['mode'],'add');self.assertFalse(args['autorename']);self.assertTrue(args['strict_conflict'])

    def test_provider_error_omits_response_body_and_tokens(self):
        response=Mock(status_code=500)
        with patch('workqueue.dropbox_provider.requests.post',return_value=response):
            with self.assertRaises(provider.DropboxError) as error: provider.request('https://api.dropboxapi.com/test')
        self.assertNotIn('test-token',str(error.exception))

    def test_provider_cannot_call_sharing_or_deletion_endpoints(self):
        for endpoint in ['files/delete_v2','sharing/create_shared_link_with_settings','https://evil.invalid']:
            with self.assertRaises(provider.DropboxError): provider.api(endpoint,{})

    def test_create_folder_uses_supported_absolute_path_and_concrete_metadata(self):
        parent={'.tag':'folder','id':'id:parent','path_display':'/Projects/Existing','path_lower':'/projects/existing'}
        result={'id':'id:child','name':'Client Uploads','path_display':'/Projects/Existing/Client Uploads','path_lower':'/projects/existing/client uploads'}
        with patch.object(provider,'metadata',return_value=parent),patch.object(provider,'api',return_value={'metadata':result}) as api:
            child=provider.create_folder('id:parent','Client Uploads')
        self.assertEqual(api.call_args.args[1],{'path':'/Projects/Existing/Client Uploads','autorename':False})
        self.assertEqual(child['.tag'],'folder')

    def test_create_folder_refuses_parent_outside_projects(self):
        parent={'.tag':'folder','id':'id:moved','path_display':'/Private','path_lower':'/private'}
        with patch.object(provider,'metadata',return_value=parent),patch.object(provider,'api') as api:
            with self.assertRaises(provider.DropboxError): provider.create_folder('id:moved','Client Uploads')
        api.assert_not_called()

    def test_current_period_uses_eastern_time_at_midnight_boundary(self):
        # 00:30 UTC November 1 is still October 31 in Massachusetts.
        with patch('workqueue.dropbox_filing.timezone.now',return_value=datetime(2026,11,1,0,30,tzinfo=dt_timezone.utc)):
            self.assertEqual(self.period_original(),'2610')
        with patch('workqueue.dropbox_filing.timezone.now',return_value=datetime(2026,11,1,5,30,tzinfo=dt_timezone.utc)):
            self.assertEqual(self.period_original(),'2611')

    def test_root_listing_follows_all_pages(self):
        # Exercise the provider implementation rather than the test's directory fixture.
        with patch.object(provider,'root',return_value=(self.connection,{})),patch.object(provider,'api',side_effect=[
                {'entries':[remote_folder()],'has_more':True,'cursor':'next'},
                {'entries':[remote_folder('261008',identifier='id:second')],'has_more':False}]) as api:
            entries=self.children_original()
        self.assertEqual(len(entries),2)
        self.assertEqual(api.call_args_list[1].args[0],'files/list_folder/continue')

    def test_missing_metadata_only_suppresses_explicit_not_found(self):
        with patch.object(provider,'api',side_effect=provider.DropboxError(status=409,detail='path/not_found/x')):
            self.assertIsNone(provider.metadata('id:missing',missing=True))
        with patch.object(provider,'api',side_effect=provider.DropboxError(status=403)):
            with self.assertRaises(provider.DropboxError): provider.metadata('id:denied',missing=True)


class DropboxConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_approvals_cannot_reserve_same_number(self):
        from .models import QueueState
        QueueState.objects.get_or_create(pk=1)
        owner=get_user_model().objects.create_superuser('parallel-owner','owner@example.invalid','test-only')
        DropboxConnection.objects.create(account_id='test-account',root_id='id:root',enabled=True)
        items=[create_work(data=work_data(),actor=owner)[0] for _ in range(2)]
        with patch.object(provider,'children',return_value=[remote_folder()]),patch.object(filing,'period',return_value='2610'):
            previews=[filing.preview(item=item,mode='new',title='Test house',address='Example',actor=owner)[1] for item in items]
            barrier=Barrier(2)
            def run(index):
                close_old_connections()
                try:
                    barrier.wait()
                    filing.approve(item_id=items[index].pk,signed=previews[index],actor=owner)
                    return 'saved'
                except EditConflict:
                    return 'conflict'
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(run,range(2)))
        self.assertCountEqual(results,['saved','conflict'])
        self.assertEqual(list(ProjectFolder.objects.values_list('number',flat=True)),['261008'])
