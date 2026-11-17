from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail, signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction
from django.test import Client as TestClient, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from . import project_registration as registration
from .dropbox_models import DropboxConnection, DropboxFolder, ProjectFolder, DropboxFiling, ProjectMonthSequence
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink, FreshBooksInvoice
from .freshbooks_project_models import FreshBooksProject, FreshBooksClientProvision
from .models import (Client, ClientContact, WorkProject, WorkItem, Submission, QueueState,
    NotificationDelivery, ProjectAccess, ProjectRegistration, EmailAccessToken)
from .pm_models import ProjectEvent, ProposalInvitation
from .portal_models import PortalMember
from .portal_notice_models import PortalInvitation
from .queue_project_updates import initial_for_update


class RegistrationFixture:
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('registrar', 'staff@example.invalid', 'test-only')
        self.crm = Client.objects.create(name='Alex Example', billing_street='1 Sample Lane', billing_city='Example City')
        self.contact = ClientContact.objects.create(client=self.crm, full_name='Alex Example', email='alex@example.invalid', phone='5550100')
        self.fb = FreshBooksConnection.objects.create(account_id='TEST123', business_id=77, business_name='Example Studio',
            encrypted_tokens='mocked', granted_scopes=['user:projects:read', 'user:clients:read'])
        FreshBooksClientLink.objects.create(client=self.crm, account_id='TEST123', customer_id=7, label='Alex Example')
        self.db = DropboxConnection.objects.create(account_id='db-example', account_email='staff@example.invalid',
            namespace_id='namespace-example', root_id='id:root', enabled=True)
        self.root = {'.tag': 'folder', 'id': 'id:root', 'path_lower': '/projects', 'path_display': '/Projects'}
        self.folder = {'.tag': 'folder', 'id': 'id:example', 'name': '261101 - Example House - 1 Sample Lane',
            'path_lower': '/projects/261101 - example house - 1 sample lane',
            'path_display': '/Projects/261101 - Example House - 1 Sample Lane'}
        self.entries = [self.folder]
        DropboxFolder.objects.create(id=self.folder['id'], name=self.folder['name'], path=self.folder['path_display'], number='261101')
        self.remote = {'id': 42, 'client_id': 7, 'title': self.folder['name'], 'active': True}
        self.customer = {'id': 7, 'fname': 'Alex', 'lname': 'Example', 'vis_state': 0}
        self.business = {'id': 77, 'account_id': 'TEST123'}
        self.db_identity = {'account_id': 'db-example', 'root_info': {'root_namespace_id': 'namespace-example'}}
        self.data = dict(contact=str(self.contact.pk), folder='id:example', remote_id=42,
            name='Example House', street='1 Sample Lane', city='Example City', state='VT', zip_code='05001')
        for target, handler in [('workqueue.freshbooks.api', self.fb_api), ('workqueue.dropbox_provider.api', self.db_api)]:
            mocker = patch(target, side_effect=handler)
            mocker.start(); self.addCleanup(mocker.stop)
        self.client.force_login(self.owner)
        self.url = reverse('workqueue:register_existing_project')
        QueueState.objects.get_or_create(pk=1)

    def fb_api(self, method, path, **kwargs):
        self.assertEqual(method, 'GET', 'Registration must never write provider records')
        if path == '/auth/api/v1/users/me':
            return {'response': {'business_memberships': [{'role': 'owner', 'business': self.business}]}}
        if path == '/accounting/account/TEST123/users/clients/7':
            return {'response': {'result': {'client': self.customer}}}
        if path == '/projects/business/77/project/42':
            return {'project': self.remote}
        if path == '/projects/business/77/projects':
            return {'projects': [self.remote], 'meta': {'pages': 1}}
        raise AssertionError(path)

    def db_api(self, endpoint, data, **kwargs):
        if endpoint == 'files/get_metadata' and data == {'path': 'id:root'}:
            return self.root
        if endpoint == 'users/get_current_account':
            return self.db_identity
        if endpoint == 'files/list_folder' and data['path'] == 'id:root':
            return {'entries': self.entries, 'has_more': False}
        raise AssertionError('Unexpected Dropbox operation: ' + endpoint)

    def preview(self):
        return registration.preview(data=self.data, actor=self.owner)

    def register(self):
        return registration.register(signed=self.preview()[1], actor=self.owner)

    def assert_no_work_or_side_effects(self):
        for model in (WorkItem, Submission, NotificationDelivery, ProjectAccess, DropboxFiling,
                      FreshBooksInvoice, FreshBooksClientProvision, PortalMember, PortalInvitation,
                      ProposalInvitation, EmailAccessToken, ProjectMonthSequence):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertEqual(QueueState.objects.get(pk=1).last_reference_number, 0)
        self.assertEqual(QueueState.objects.get(pk=1).last_queue_order, 0)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(WORK_QUEUE_ENABLED=True, BOOKING_ENABLED=False)
class RegistrationTests(RegistrationFixture, TestCase):
    def test_preview_and_register_only_native_identity_then_repeat(self):
        snapshot, signed, existing = self.preview()
        self.assertIsNone(existing)
        self.assertFalse(WorkProject.objects.exists())
        project, created = registration.register(signed=signed, actor=self.owner)
        self.assertTrue(created)
        self.assertEqual(project.registration.contact, self.contact)
        self.assertEqual(project.filing_folder.number, '261101')
        self.assertIsNone(project.canonical_reference)  # Queue receipt IDs remain separate.
        self.assertEqual(project.freshbooks_project.state, 'linked')
        self.assertIsNone(project.freshbooks_project.work_item_id)
        self.assertEqual(project.freshbooks_project.remote_id, 42)
        self.assertEqual(project.registration.snapshot, snapshot)
        self.assertEqual(ProjectEvent.objects.get().actor, self.owner)
        self.assertEqual(registration.register(signed=signed, actor=self.owner), (project, False))
        self.assertEqual(self.register(), (project, False))
        self.assertEqual(WorkProject.objects.count(), 1)
        self.assertEqual(ProjectEvent.objects.count(), 1)
        self.assert_no_work_or_side_effects()

    def test_genuine_first_update_through_normal_queue_form(self):
        project, _ = self.register()
        self.assert_no_work_or_side_effects()
        response = self.client.get(reverse('workqueue:queue'), {'update_project': project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['create_form'].initial['contact_email'], self.contact.email)
        self.assertContains(response, '261101')
        data = initial_for_update(project)
        data.update(submission_token=uuid.uuid4(), description='Revise the fictional kitchen layout', service_needed='Plan changes')
        post = {'action': 'create', **{'new-' + key: str(value) if value is not None else '' for key, value in data.items()}}
        self.assertEqual(self.client.post(reverse('workqueue:queue'), post).status_code, 302)
        item = WorkItem.objects.get()
        self.assertEqual(item.kind, 'update')
        self.assertEqual(item.project, project)
        self.assertEqual(item.client_contact, self.contact)
        self.assertIsNone(item.previous_request)
        self.assertEqual(item.submission_position, 1)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(WorkProject.objects.count(), 1)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.post(reverse('workqueue:queue'), post).status_code, 302)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_mismatched_client_and_missing_explicit_link_rejected(self):
        self.remote['client_id'] = 8
        with self.assertRaisesMessage(ValidationError, 'different billing client'):
            self.register()
        self.remote['client_id'] = 7
        FreshBooksClientLink.objects.all().delete()
        with self.assertRaisesMessage(ValidationError, 'billing client first'):
            self.register()
        self.assertFalse(WorkProject.objects.exists())

    def test_duplicate_and_mismatched_numbers_and_unavailable_folder(self):
        for title in ('261102 - Other', 'Unnumbered project'):
            self.remote['title'] = title
            with self.assertRaises(ValidationError): self.register()
        self.remote['title'] = self.folder['name']
        self.entries.append({**self.folder, 'id': 'id:duplicate'})
        with self.assertRaisesMessage(ValidationError, 'More than one'): self.register()
        self.entries.clear()
        with self.assertRaises(ValidationError): self.register()
        self.assertFalse(WorkProject.objects.exists())

    def test_remote_changes_after_review_rejected(self):
        signed = self.preview()[1]
        self.remote['title'] += ' changed'
        with self.assertRaisesMessage(ValidationError, 'details changed'):
            registration.register(signed=signed, actor=self.owner)
        self.assertFalse(WorkProject.objects.exists())

    def test_contact_and_account_changes_rejected(self):
        signed = self.preview()[1]
        self.contact.full_name = 'Changed contact'; self.contact.save()
        with self.assertRaises(ValidationError): registration.register(signed=signed, actor=self.owner)
        self.business['account_id'] = 'DIFFERENT'
        with self.assertRaisesMessage(ValidationError, 'connected identity'): self.preview()
        self.business['account_id'] = 'TEST123'
        self.db_identity['account_id'] = 'different-dropbox'
        with self.assertRaisesMessage(ValidationError, 'Dropbox account'): self.preview()

    def test_archived_and_merged_crm_rejected(self):
        from django.utils import timezone
        self.contact.archived_at = timezone.now(); self.contact.save()
        with self.assertRaises(ValidationError): self.preview()
        self.contact.archived_at = None; self.contact.save()
        self.crm.merged_into = Client.objects.create(name='Other'); self.crm.save()
        with self.assertRaises(ValidationError): self.preview()

    def test_existing_provider_or_number_collision_never_adopted(self):
        other = WorkProject.objects.create(name='Another project')
        binding = ProjectFolder.objects.create(project=other, folder_id='id:example', number='261101', intended_name=self.folder['name'])
        with self.assertRaisesMessage(ValidationError, 'already connected'): self.preview()
        binding.delete()
        other.canonical_reference = '261101'; other.save()
        with self.assertRaisesMessage(ValidationError, 'already connected'): self.preview()
        other.canonical_reference = None; other.save()
        FreshBooksProject.objects.create(project=other, client=self.crm, account_id='TEST123', business_id=77,
            remote_id=42, customer_id=7, title='Other', marker='other', state='linked')
        with self.assertRaisesMessage(ValidationError, 'already connected'): self.preview()
        self.assertEqual(WorkProject.objects.count(), 1)

    def test_atomic_rollback(self):
        signed = self.preview()[1]
        with patch('workqueue.project_registration.ProjectEvent.objects.create', side_effect=IntegrityError):
            with self.assertRaises(ValidationError): registration.register(signed=signed, actor=self.owner)
        for model in (WorkProject, ProjectFolder, FreshBooksProject, ProjectRegistration):
            self.assertFalse(model.objects.exists())
        self.assert_no_work_or_side_effects()

    def test_null_request_link_cannot_be_queued(self):
        project, _ = self.register()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FreshBooksProject.objects.filter(project=project).update(state='queued')

    def test_oauth_refresh_survives_local_rollback_and_connection_change_is_rejected(self):
        signed = self.preview()[1]
        verify = registration.verify
        def refreshed(data):
            result = verify(data)
            FreshBooksConnection.objects.filter(pk=1).update(encrypted_tokens='refreshed-fictional-token')
            return result
        with patch.object(registration, 'verify', side_effect=refreshed), patch(
                'workqueue.project_registration.ProjectEvent.objects.create', side_effect=IntegrityError):
            with self.assertRaises(ValidationError): registration.register(signed=signed, actor=self.owner)
        self.fb.refresh_from_db()
        self.assertEqual(self.fb.encrypted_tokens, 'refreshed-fictional-token')
        self.assertFalse(WorkProject.objects.exists())
        def changed(data):
            result = verify(data)
            FreshBooksConnection.objects.filter(pk=1).update(business_id=88)
            return result
        with patch.object(registration, 'verify', side_effect=changed):
            with self.assertRaisesMessage(ValidationError, 'connection changed during verification'):
                registration.register(signed=signed, actor=self.owner)
        self.assertFalse(WorkProject.objects.exists())

    def test_existing_queue_and_allocators_are_unchanged(self):
        from .services import create_work
        from .tests import work_data
        item, _ = create_work(data=work_data(), actor=self.owner)
        before = list(WorkItem.objects.values())
        allocation = QueueState.objects.values().get(pk=1)
        self.register()
        self.assertEqual(list(WorkItem.objects.values()), before)
        self.assertEqual(QueueState.objects.values().get(pk=1), allocation)
        self.assertEqual(Submission.objects.get().work_item, item)
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_unavailable_providers_fail_without_partial_registration(self):
        from .dropbox_provider import DropboxError
        from .freshbooks import BillingError
        signed = self.preview()[1]
        with patch('workqueue.project_registration.remote_project', side_effect=BillingError('Unavailable')):
            self.assertEqual(self.client.post(self.url, {'action': 'register', 'signed': signed}).status_code, 400)
        with patch('workqueue.project_registration.dropbox.root', side_effect=DropboxError('Unavailable')):
            self.assertEqual(self.client.post(self.url, {'action': 'register', 'signed': signed}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {'action': 'register', 'signed': 'invalid'}).status_code, 400)
        self.assertFalse(WorkProject.objects.exists())
        self.assert_no_work_or_side_effects()

    def test_duplicate_freshbooks_number_and_inactive_records_rejected(self):
        with patch('workqueue.project_registration.projects', return_value=[self.remote, {**self.remote, 'id': 43}]):
            with self.assertRaisesMessage(ValidationError, 'ambiguous'): self.preview()
        self.remote['active'] = False
        with self.assertRaisesMessage(ValidationError, 'active FreshBooks project'): self.preview()
        self.remote['active'] = True
        self.customer['vis_state'] = 1
        from .freshbooks import BillingError
        with self.assertRaises(BillingError): self.preview()
        self.assertFalse(WorkProject.objects.exists())

    def test_refresh_only_updates_local_folder_index(self):
        stale = DropboxFolder.objects.create(id='id:stale', name='Old folder', path='/Projects/Old folder')
        self.entries = [{**self.folder, 'name': '261101 - Renamed example', 'path_display': '/Projects/261101 - Renamed example'}]
        self.assertEqual(self.client.post(self.url, {'action': 'refresh'}).status_code, 302)
        refreshed = DropboxFolder.objects.get(pk=self.folder['id'])
        self.assertEqual(refreshed.name, self.entries[0]['name'])
        self.assertEqual(refreshed.path, self.entries[0]['path_display'])
        self.assertTrue(refreshed.available)
        stale.refresh_from_db()
        self.assertFalse(stale.available)
        self.assertFalse(WorkProject.objects.exists())
        self.assert_no_work_or_side_effects()

    def test_slow_dropbox_pagination_keeps_previous_index(self):
        from . import dropbox_provider as provider
        def paginated(endpoint, data, **kwargs):
            if endpoint == 'files/list_folder':
                return {'entries': [], 'has_more': True, 'cursor': 'fictional-cursor'}
            return self.db_api(endpoint, data, **kwargs)
        with patch.object(provider, 'api', side_effect=paginated), patch.object(provider.time, 'monotonic', side_effect=[0, 21]):
            response = self.client.post(self.url, {'action': 'refresh'})
        self.assertContains(response, 'took too long', status_code=400)
        self.assertTrue(DropboxFolder.objects.get(pk=self.folder['id']).available)
        self.assertFalse(WorkProject.objects.exists())
        self.assert_no_work_or_side_effects()

    def test_registered_project_billing_reuse_after_client_merge_and_undo(self):
        from .crm import merge_clients, undo_merge
        from . import freshbooks_projects
        from .models import ClientEvent
        project, _ = self.register()
        target = Client.objects.create(name='Surviving Example')
        merged = merge_clients(source_id=self.crm.pk, source_version=self.crm.version,
            target_id=target.pk, target_version=target.version, actor=self.owner)
        item = WorkItem(project=project, client_contact=ClientContact.objects.get(pk=self.contact.pk))
        with patch.object(freshbooks_projects, 'configuration', return_value=(self.fb, 77)):
            self.assertEqual(freshbooks_projects.plan(item, 'Example', action='link')['action'], 'reuse')
            FreshBooksClientLink.objects.filter(client=merged).update(customer_id=99)
            with self.assertRaisesMessage(ValidationError, 'another FreshBooks client'):
                freshbooks_projects.plan(item, 'Example', action='link')
            FreshBooksClientLink.objects.filter(client=merged).update(customer_id=7)
            event = ClientEvent.objects.get(client=merged, action='Client records merged')
            undo_merge(client_id=merged.pk, version=merged.version, event_id=event.pk, actor=self.owner)
            item.client_contact = ClientContact.objects.get(pk=self.contact.pk)
            self.assertEqual(freshbooks_projects.plan(item, 'Example', action='link')['action'], 'reuse')
        self.assertEqual(FreshBooksProject.objects.get(project=project).client_id, self.crm.pk)
        self.assert_no_work_or_side_effects()

    def test_signed_review_tamper_expiry_and_actor(self):
        signed = self.preview()[1]
        other = get_user_model().objects.create_superuser('other', 'other@example.invalid', 'test-only')
        with self.assertRaises(ValidationError): registration.register(signed=signed, actor=other)
        with self.assertRaises(ValidationError): registration.register(signed=signed + 'x', actor=self.owner)
        with patch('django.core.signing.time.time', return_value=10**12):
            with self.assertRaises(ValidationError): registration.register(signed=signed, actor=self.owner)
        self.assertFalse(WorkProject.objects.exists())

    def test_permissions_feature_flag_csrf_and_method(self):
        user = get_user_model().objects.create_user('limited', is_staff=True)
        for codename in ('view_workitem', 'change_workitem', 'add_workitem'):
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        with self.assertRaises(PermissionDenied): registration.preview(data=self.data, actor=user)
        user.user_permissions.add(Permission.objects.get(codename='add_workproject'))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.put(self.url).status_code, 405)
        csrf = TestClient(enforce_csrf_checks=True); csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, {'action': 'register'}).status_code, 403)
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=True):
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assert_no_work_or_side_effects()

    def test_review_ui_entry_points_and_registered_workspace(self):
        self.assertContains(self.client.get(self.url), 'Verify and review')
        self.assertContains(self.client.get(reverse('workqueue:queue')), 'Register existing project')
        self.assertContains(self.client.get(reverse('workqueue:projects')), 'Register existing project')
        response = self.client.post(self.url, {'action': 'preview', **self.data})
        self.assertContains(response, 'Review verified identities')
        self.assertContains(response, 'client ID 7')
        self.assertIn('no-store', response['Cache-Control'])
        self.assert_no_work_or_side_effects()
        response = self.client.post(self.url, {'action': 'register', 'signed': response.context['signed']})
        self.assertEqual(response.status_code, 302)
        project = WorkProject.objects.get()
        self.assertContains(self.client.get(response.url), 'Add update to this project')
        self.assertContains(self.client.get(reverse('workqueue:projects'), {'scope': 'all'}), project.name)
        self.assert_no_work_or_side_effects()


@override_settings(WORK_QUEUE_ENABLED=True, BOOKING_ENABLED=False)
class RegistrationConcurrencyTests(RegistrationFixture, TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_approvals_create_one_project_and_no_work(self):
        signed = self.preview()[1]
        barrier = Barrier(2)
        def run(_):
            try:
                barrier.wait(timeout=10)
                return registration.register(signed=signed, actor=self.owner)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [0, 1]))
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(WorkProject.objects.count(), 1)
        self.assertEqual(ProjectRegistration.objects.count(), 1)
        self.assert_no_work_or_side_effects()

    @skipUnlessDBFeature('has_select_for_update')
    def test_conflicting_concurrent_reviews_save_only_one_project(self):
        first = self.preview()[1]
        self.data['name'] = 'Different local description'
        second = self.preview()[1]
        barrier = Barrier(2)
        def run(signed):
            try:
                barrier.wait(timeout=10)
                try:
                    return registration.register(signed=signed, actor=self.owner)[1]
                except ValidationError:
                    return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run, [first, second])), [False, True])
        self.assertEqual(WorkProject.objects.count(), 1)
        self.assertEqual(ProjectRegistration.objects.count(), 1)
        self.assert_no_work_or_side_effects()
