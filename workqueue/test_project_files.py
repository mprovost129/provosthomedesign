import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .access import read_intake_token, session_digest
from .models import Attachment, CompletedDelivery, NotificationDelivery, PendingUpload, ProjectAccess, QueueState, Submission, WorkItem
from .pm_models import ProjectEvent
from .portal_models import PortalDocument
from .services import create_work
from .test_client_intake import valid_pdf
from .tests import work_data
from .uploads import checked_name, validate_content


def mpo_original():
    """Synthetic two-frame JPEG/MPF fixture; never use a client document."""
    output = BytesIO()
    Image.new('RGB', (4, 4), 'red').save(output, format='MPO', save_all=True,
        append_images=[Image.new('RGB', (4, 4), 'blue')])
    return output.getvalue()


@override_settings(DEBUG=True, WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, INTAKE_LOCAL_DEVELOPMENT=True,
    INTAKE_DIRECT_UPLOADS=False, RECAPTCHA_ENTERPRISE_API_KEY='', RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='')
class ProjectFilesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = override_settings(STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
            'intake_private': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': self.directory.name, 'base_url': None}}})
        self.storage.enable()
        self.addCleanup(self.storage.disable)
        self.owner = get_user_model().objects.create_superuser('file-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.project = self.item.project
        self.client.force_login(self.owner)
        self.url = reverse('workqueue:project_add_files', args=[self.project.pk])
        self.project_url = reverse('workqueue:project_detail', args=[self.project.pk])
        self.page = self.client.get(self.url)
        self.token = self.page.context['form'].initial['intake_token']
        self.nonce = read_intake_token(self.page.wsgi_request, self.token)
        self.binding = session_digest(self.page.wsgi_request)

    def upload(self, name='survey.pdf'):
        result = self.client.post(reverse('workqueue:start_upload'), {
            'intake_token': self.token, 'upload_id': str(uuid.uuid4()), 'file': valid_pdf(name)})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['id']

    def save(self, ids, **changes):
        data = {'intake_token': self.token, 'work_item': str(self.item.pk), 'upload_ids': ','.join(ids)}
        data.update(changes)
        return self.client.post(self.url, data)

    def test_upload_save_reopen_download_and_replay_leave_queue_and_delivery_unchanged(self):
        before = list(WorkItem.objects.values())
        allocation = list(QueueState.objects.values())
        notices = NotificationDelivery.objects.count()
        self.assertContains(self.client.get(self.project_url), 'Add files')
        ids = [self.upload(), self.upload('photo.pdf')]
        self.assertRedirects(self.save(ids), self.project_url + '#files')
        self.assertEqual(Attachment.objects.count(), 2)
        self.assertEqual(list(WorkItem.objects.values()), before)
        self.assertEqual(list(QueueState.objects.values()), allocation)
        self.assertEqual(NotificationDelivery.objects.count(), notices)
        self.assertFalse(CompletedDelivery.objects.exists())
        self.assertFalse(PortalDocument.objects.exists())
        self.assertEqual(ProjectEvent.objects.filter(action='Source files added').count(), 1)
        reopened = self.client.get(self.project_url)
        self.assertContains(reopened, 'survey.pdf')
        self.assertContains(reopened, 'photo.pdf')
        self.assertNotContains(reopened, self.binding)
        attachment = Attachment.objects.get(original_name='survey.pdf')
        download = self.client.get(reverse('workqueue:download', args=[attachment.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertIn(b'%PDF-', b''.join(download.streaming_content))
        self.assertRedirects(self.save(ids), self.project_url + '#files')
        self.assertEqual(Attachment.objects.count(), 2)
        self.assertEqual(Submission.objects.filter(channel='staff_files').count(), 1)
        counts = (Attachment.objects.count(), Submission.objects.count(), ProjectEvent.objects.count())
        self.assertEqual(self.save(ids[:1]).status_code, 400)
        self.assertEqual((Attachment.objects.count(), Submission.objects.count(), ProjectEvent.objects.count()), counts)

    def test_failed_file_blocks_entire_batch_then_retry_attaches_once(self):
        first, second = self.upload(), self.upload('second.pdf')
        PendingUpload.objects.filter(pk=second).update(state='reserved')
        response = self.save([first, second])
        self.assertContains(response, 'No files were added', status_code=400)
        self.assertFalse(Attachment.objects.exists())
        self.assertEqual(PendingUpload.objects.get(pk=first).state, 'ready')
        PendingUpload.objects.filter(pk=second).update(state='ready')
        self.assertEqual(self.save([first, second]).status_code, 302)
        self.assertEqual(Attachment.objects.count(), 2)

    def test_crm_and_queue_identify_files_as_staff_sources(self):
        from .crm_overview import timeline
        self.save([self.upload()])
        client = self.item.client_contact.client
        page = timeline(client, 1)
        source_events = [entry for entry in page.object_list if entry['title'].startswith('Source files added by staff')]
        self.assertEqual(len(source_events), 1)
        self.assertEqual(source_events[0]['files'][0][0], 'survey.pdf')
        self.assertContains(self.client.get(reverse('workqueue:client_detail', args=[client.pk])), 'Source files added by staff')
        self.assertContains(self.client.get(reverse('workqueue:queue')), 'Source files added by staff')

    def test_database_failure_rolls_back_all_metadata_and_is_retryable(self):
        ids = [self.upload(), self.upload('second.pdf')]
        original_create = Attachment.objects.create
        calls = 0

        def failing_create(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('simulated failure')
            return original_create(**kwargs)

        with patch('workqueue.project_files.Attachment.objects.create', side_effect=failing_create), \
                self.assertLogs('workqueue.project_file_views', level='ERROR') as captured:
            response = self.save(ids)
        self.assertIn('RuntimeError', captured.output[0])
        self.assertIn('attach_project_files', captured.output[0])
        self.assertNotIn('RuntimeError: simulated failure', captured.output[0])
        self.assertContains(response, 'could not be confirmed', status_code=503)
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(Submission.objects.filter(channel='staff_files').exists())
        self.assertEqual(PendingUpload.objects.filter(state='ready').count(), 2)
        self.assertEqual(self.save(ids).status_code, 302)
        self.assertEqual(Attachment.objects.count(), 2)

    def test_uncertain_response_after_commit_can_be_retried_without_duplicates(self):
        from .project_files import attach_project_files
        ids = [self.upload()]

        def save_then_disconnect(**kwargs):
            attach_project_files(**kwargs)
            raise OSError('response lost')

        with patch('workqueue.project_file_views.attach_project_files', side_effect=save_then_disconnect):
            response = self.save(ids)
            self.assertEqual(response.status_code, 503)
        # The uploader rebuilds its hidden list from the rendered rows on retry.
        rendered_ids = [str(upload.pk) for upload in response.context['ready_uploads']]
        self.assertEqual(rendered_ids, ids)
        self.assertContains(response, 'Saved; retry to confirm', status_code=503)
        self.assertEqual(self.save(rendered_ids).status_code, 302)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(ProjectEvent.objects.filter(action='Source files added').count(), 1)

    def test_invalid_expired_foreign_and_duplicate_uploads_rejected(self):
        upload = self.upload()
        for ids in [[], ['invalid'], [upload, upload], [str(uuid.uuid4()) for _ in range(11)]]:
            self.assertEqual(self.save(ids).status_code, 400)
        PendingUpload.objects.filter(pk=upload).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.save([upload]).status_code, 400)
        PendingUpload.objects.filter(pk=upload).update(expires_at=timezone.now()+timedelta(hours=1), session_digest='other')
        self.assertEqual(self.save([upload]).status_code, 400)
        self.assertFalse(Attachment.objects.exists())

    def test_other_project_and_deleted_job_rejected(self):
        other = create_work(data=work_data(project_name='Other project'), actor=self.owner)[0]
        upload = self.upload()
        self.assertEqual(self.save([upload], work_item=str(other.pk)).status_code, 400)
        WorkItem.objects.filter(pk=self.item.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.save([upload]).status_code, 400)
        self.assertFalse(Attachment.objects.exists())

    def test_staff_permissions_preview_feature_flags_and_csrf(self):
        guest = Client()
        self.assertEqual(guest.get(self.url).status_code, 302)
        self.assertEqual(guest.post(self.url, {}).status_code, 302)
        reader = get_user_model().objects.create_user('reader', is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(reader)
        self.assertNotContains(self.client.get(self.project_url), 'Add files')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        self.client.force_login(self.owner)
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=True):
            self.assertEqual(self.client.get(self.url).status_code, 403)
        with override_settings(WORK_INTAKE_ENABLED=False):
            self.assertNotContains(self.client.get(self.project_url), 'Add files')
            self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, {}).status_code, 403)

    def test_staff_files_are_not_exposed_to_client_tracking_downloads_or_receipts(self):
        self.save([self.upload('staff-source.pdf')])
        attachment = Attachment.objects.get()
        client = Client()
        session = client.session
        session['queue_verified_email'] = 'collaborator@example.invalid'
        session['queue_email_verified_until'] = (timezone.now()+timedelta(hours=1)).timestamp()
        session.save()
        ProjectAccess.objects.create(project=self.project, email='collaborator@example.invalid')
        self.assertNotContains(client.get(reverse('workqueue:tracking')), 'staff-source.pdf')
        self.assertEqual(client.get(reverse('workqueue:download', args=[attachment.pk])).status_code, 404)
        self.assertEqual(client.get(reverse('workqueue:receipt_download', args=[attachment.submission_id])).status_code, 404)

    def test_existing_content_validation_and_storage_error_are_not_successful_uploads(self):
        response = self.client.post(reverse('workqueue:start_upload'), {'intake_token': self.token,
            'file': SimpleUploadedFile('fake.pdf', b'not a PDF')})
        self.assertEqual(response.status_code, 400)
        with patch('workqueue.uploads.private_storage', side_effect=OSError('offline')):
            response = self.client.post(reverse('workqueue:start_upload'), {'intake_token': self.token, 'file': valid_pdf()})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PendingUpload.objects.filter(state='ready').exists())
        self.assertFalse(Attachment.objects.exists())

    def test_jpeg_mpo_original_upload_is_accepted_and_downloaded_byte_for_byte(self):
        original = mpo_original()
        self.assertEqual(Image.open(BytesIO(original)).format, 'MPO')
        for suffix in ['jpg', 'jpeg']:
            with self.subTest(suffix=suffix):
                stream = BytesIO(original)
                validate_content(stream, f'phone.{suffix}')
                self.assertEqual(stream.tell(), 0)
                self.assertEqual(stream.read(), original)
        response = self.client.post(reverse('workqueue:start_upload'), {'intake_token': self.token,
            'file': SimpleUploadedFile('phone.jpeg', original, content_type='image/jpeg')})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.save([response.json()['id']]).status_code, 302)
        attachment = Attachment.objects.get()
        self.assertEqual(attachment.content_type, 'image/jpeg')
        self.assertEqual(attachment.size_bytes, len(original))
        download = self.client.get(reverse('workqueue:download', args=[attachment.pk]))
        self.assertEqual(b''.join(download.streaming_content), original)

    def test_mpo_support_keeps_image_format_corruption_and_bomb_checks(self):
        original = mpo_original()
        for name, content in [('wrong.png', original), ('wrong.webp', original),
                              ('broken.jpeg', b'not a jpeg'), ('truncated.jpg', original[:32])]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_content(BytesIO(content), name)
        png = BytesIO()
        Image.new('RGB', (4, 4)).save(png, format='PNG')
        with self.assertRaises(ValidationError):
            validate_content(png, 'disguised.jpeg')
        with self.assertRaises(ValidationError):
            checked_name('unsupported.mpo', len(original))
        with patch('PIL.Image.MAX_IMAGE_PIXELS', 10), self.assertRaises(ValidationError):
            validate_content(BytesIO(original), 'large.jpeg')


class ProjectFileConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_batch_retries_attach_once(self):
        from .project_files import attach_project_files
        from .uploads import reserve_upload
        QueueState.objects.get_or_create(pk=1)
        owner = get_user_model().objects.create_superuser('file-race-owner', 'owner@example.invalid', 'test-only')
        item = create_work(data=work_data(), actor=owner)[0]
        nonce, binding = uuid.uuid4(), 'test-session'
        upload = reserve_upload(nonce=nonce, binding=binding, name='race.pdf', size=20)
        PendingUpload.objects.filter(pk=upload.pk).update(state='ready')
        start = Barrier(2)

        def save_batch(_):
            close_old_connections()
            try:
                start.wait(timeout=10)
                saved, created = attach_project_files(project=item.project, item_id=item.pk,
                    nonce=nonce, binding=binding, ids=str(upload.pk), actor=owner)
                return saved.pk, created
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save_batch, range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(ProjectEvent.objects.filter(action='Source files added').count(), 1)
