import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .access import authorized_work
from .crm_views import client_counts
from .forms import CopyWorkForm
from .models import Attachment, Client, NotificationDelivery, QueueState, Status, Submission, WorkItem
from .notifications import deliver_pending
from .ordering import order_snapshot, save_order
from .services import EditConflict, copy_work, create_work, delete_work, edit_work, restore_work
from .tests import work_data


def copied_data(source, **changes):
    data = {name: getattr(source, name) for name in CopyWorkForm.Meta.fields}
    data.update(kind='new', project=None, project_name='Separate copy', requested_deadline=None)
    data.update(changes)
    return data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
                   INTAKE_PUBLIC_BASE_URL='http://testserver', INTAKE_OWNER_EMAIL='owner@example.invalid')
class ProjectControlTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('project-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.client.force_login(self.owner)

    def test_edit_shared_project_updates_tracking_and_stale_sibling_guard(self):
        sibling = create_work(data=work_data(kind='update', project=self.item.project), actor=self.owner)[0]
        before_answers = self.item.submissions.get().answers.copy()
        edit_work(item_id=self.item.pk, version=1, data={'project_name': 'Corrected addition',
            'project_city': 'Boston', 'description': 'Updated scope', 'service_needed': 'Framing'}, actor=self.owner)
        self.item.refresh_from_db()
        sibling.refresh_from_db()
        self.assertEqual(self.item.project.name, 'Corrected addition')
        self.assertEqual(self.item.project.city, 'Boston')
        self.assertEqual(self.item.description, 'Updated scope')
        self.assertEqual(self.item.submissions.get().answers, before_answers)
        self.assertEqual(sibling.version, 2)
        with self.assertRaises(EditConflict):
            edit_work(item_id=sibling.pk, version=1, data={'status': Status.COMPLETED}, actor=self.owner)
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_reconnecting_does_not_overwrite_target_with_old_project_defaults(self):
        other = create_work(data=work_data(project_name='Other project', project_city='Providence'), actor=self.owner)[0]
        edit_work(item_id=self.item.pk, version=1, data={'project': other.project,
            'project_name': self.item.project.name, 'project_city': self.item.project.city}, actor=self.owner)
        other.project.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(other.project.name, 'Other project')
        self.assertEqual(other.project.city, 'Providence')
        self.assertEqual(self.item.project_name, 'Other project')

    def test_copy_has_own_reference_position_no_files_notices_schedule_and_idempotency(self):
        Attachment.objects.create(submission=self.item.submissions.get(), original_name='plan.pdf', storage_key='private/original.pdf')
        self.item.internal_notes = 'Private note'; self.item.status = Status.COMPLETED; self.item.save()
        token = uuid.uuid4()
        copied, created = copy_work(item_id=self.item.pk, version=self.item.version,
            data=copied_data(self.item), token=token, actor=self.owner)
        repeated, again = copy_work(item_id=self.item.pk, version=self.item.version,
            data=copied_data(self.item), token=token, actor=self.owner)
        self.assertTrue(created); self.assertFalse(again); self.assertEqual(copied.pk, repeated.pk)
        self.assertNotEqual(copied.reference, self.item.reference)
        self.assertNotEqual(copied.project_id, self.item.project_id)
        self.assertEqual(copied.client_contact_id, self.item.client_contact_id)
        self.assertEqual(copied.status, Status.NEW)
        self.assertEqual(copied.internal_notes, '')
        self.assertEqual(copied.submission_position, 1)
        self.assertFalse(copied.submissions.get().attachments.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_copy_can_be_connected_update_with_own_place(self):
        copied, _ = copy_work(item_id=self.item.pk, version=1, data=copied_data(self.item,
            kind='update', project=self.item.project), token=uuid.uuid4(), actor=self.owner)
        self.assertEqual(copied.project_id, self.item.project_id)
        self.assertEqual(copied.submission_position, 2)

    def test_delete_removes_duplicate_everywhere_without_touching_sibling_or_files(self):
        sibling = create_work(data=work_data(kind='update', project=self.item.project), actor=self.owner)[0]
        file = Attachment.objects.create(submission=self.item.submissions.get(), original_name='plan.pdf', storage_key='private/original.pdf')
        notice = NotificationDelivery.objects.create(submission=self.item.submissions.get(), recipient_kind='client',
            recipient=self.item.contact_email, state='pending', body='Do not send')
        snapshot = order_snapshot()
        delete_work(item_id=self.item.pk, version=1, actor=self.owner)
        self.assertFalse(WorkItem.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(authorized_work(self.item.contact_email).filter(pk=self.item.pk).exists())
        self.assertEqual(WorkItem.objects.with_position().get(pk=sibling.pk).position, 1)
        self.assertEqual(Attachment.objects.get(pk=file.pk).storage_key, 'private/original.pdf')
        sibling.project.refresh_from_db(); self.assertEqual(sibling.project.canonical_reference, sibling.reference)
        client = client_counts(Client.objects.filter(pk=self.item.client_contact.client_id)).get()
        self.assertEqual(client.request_count, 1); self.assertEqual(client.active_count, 1)
        notice.refresh_from_db(); self.assertEqual(notice.state, 'canceled')
        with self.assertRaises(EditConflict):
            save_order(ordered_ids=snapshot['items'] and [x['id'] for x in snapshot['items']],
                       snapshot=snapshot['snapshot'], actor=self.owner)
        deleted = self.client.get(reverse('workqueue:queue'), {'scope':'deleted'})
        self.assertContains(deleted, self.item.reference)
        self.assertContains(deleted, 'Restore request')
        normal = self.client.get(reverse('workqueue:queue'), {'scope':'all'})
        self.assertNotContains(normal, f'id="request-{self.item.pk}"')
        detail = self.client.get(reverse('workqueue:client_detail', args=[client.pk]))
        self.assertNotIn(self.item.pk, [row.pk for row in detail.context['page']])
        self.assertEqual(detail.context['projects'].get().job_count, 1)

    def test_restore_appends_active_work_without_resending_canceled_mail(self):
        delete_work(item_id=self.item.pk, version=1, actor=self.owner)
        next_job = create_work(data=work_data(), actor=self.owner)[0]
        restored = restore_work(item_id=self.item.pk, version=2, actor=self.owner)
        self.assertEqual(WorkItem.objects.with_position().get(pk=next_job.pk).position, 1)
        self.assertEqual(WorkItem.objects.with_position().get(pk=restored.pk).position, 2)
        self.assertEqual(restored.reference, 'PHD-00001')
        self.assertEqual(restored.submission_position, 1)
        self.assertEqual(restored.version, 3)

    def test_delete_permission_confirmation_and_stale_forms(self):
        url = reverse('workqueue:queue')
        post = {'action':'delete','item_id':str(self.item.pk),'version':1,'confirmed':'on'}
        self.assertEqual(self.client.post(url, dict(post, confirmed='')).status_code, 400)
        self.assertTrue(WorkItem.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self.client.post(url, dict(post,version=9)).status_code, 409)
        ordinary = get_user_model().objects.create_user('view-only', is_staff=True)
        ordinary.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(ordinary)
        self.assertEqual(self.client.post(url, post).status_code, 403)
        with self.assertRaises(PermissionDenied):
            delete_work(item_id=self.item.pk, version=1, actor=ordinary)
        self.assertNotContains(self.client.get(url), 'class="delete-request"')

    def test_copy_review_and_double_post_in_queue(self):
        url = reverse('workqueue:queue')
        response = self.client.get(url, {'copy':str(self.item.pk)})
        self.assertContains(response, f'Copy {self.item.reference} into a new request')
        self.assertEqual(WorkItem.objects.count(), 1)
        token = uuid.uuid4()
        data = {'action':'copy', 'new-source_id':str(self.item.pk), 'new-source_version':1,
                'new-submission_token':str(token)}
        for name,value in copied_data(self.item).items():
            data['new-'+name] = str(value.pk) if hasattr(value,'pk') else value or ''
        first = self.client.post(url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(WorkItem.objects.count(), 2)

    def test_deleted_jobs_cannot_be_copied_edited_tracked_or_downloaded_by_client(self):
        file = Attachment.objects.create(submission=self.item.submissions.get(), original_name='private.pdf', storage_key='private/old.pdf')
        delete_work(item_id=self.item.pk, version=1, actor=self.owner)
        self.assertEqual(self.client.get(reverse('workqueue:queue'), {'copy':self.item.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse('workqueue:queue'), {'action':'status','item_id':self.item.pk,
            'version':2,'status':'completed'}).status_code, 404)
        self.client.logout()
        session = self.client.session
        session['queue_verified_email'] = self.item.contact_email
        session['queue_email_verified_until'] = timezone.now().timestamp() + 3600
        session.save()
        tracking = self.client.get(reverse('workqueue:tracking'), {'reference':self.item.reference})
        self.assertIsNone(tracking.context['selected'])
        self.assertEqual(self.client.get(reverse('workqueue:download', args=[file.pk])).status_code, 404)

    def test_worker_rechecks_deleted_job_before_sending_requeued_email(self):
        delete_work(item_id=self.item.pk, version=1, actor=self.owner)
        notice = NotificationDelivery.objects.create(submission=self.item.submissions.get(), recipient_kind='client',
            recipient=self.item.contact_email, state='pending', body='No longer eligible')
        self.assertEqual(deliver_pending(), 0)
        notice.refresh_from_db(); self.assertEqual(notice.state, 'canceled')

    def test_old_edit_post_preserves_missing_intake_fields(self):
        response = self.client.post(reverse('workqueue:queue'), {'action':'edit','item_id':self.item.pk,
            f'{self.item.pk}-version':1, f'{self.item.pk}-status':'completed',
            f'{self.item.pk}-priority':'normal',f'{self.item.pk}-queue_order':1,
            f'{self.item.pk}-project':self.item.project_id})
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db(); self.assertEqual(self.item.project_name,'Park Street addition')
        self.assertEqual(self.item.description,'Addition design and framing coordination.')


class ConcurrentProjectCopies(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_posts_with_same_token_allocate_once(self):
        actor = get_user_model().objects.create_superuser('concurrent-project-owner', 'owner@example.invalid', 'test-only')
        QueueState.objects.get_or_create(pk=1)
        source = create_work(data=work_data(), actor=actor)[0]
        token = uuid.uuid4()
        def run(_):
            close_old_connections()
            try:
                return copy_work(item_id=source.pk, version=source.version, data=copied_data(source),
                    token=token, actor=get_user_model().objects.get(pk=actor.pk))[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run, range(2))), [False, True])
        self.assertEqual(WorkItem.objects.count(), 2)
