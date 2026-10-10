import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .crm import edit_client, merge_clients, possible_matches, save_contact, sync_clients, undo_merge
from .models import Client as CRMClient, ClientContact, ProjectAccess, QueueState, Submission, WorkItem
from .services import EditConflict, create_work
from .tests import work_data


class MatchingTests(TestCase):
    def create(self, **values):
        return create_work(data=work_data(**values), actor=None)[0]

    def test_same_normalized_email_connects_independent_requests_without_overwriting(self):
        first = self.create(contact_email="Morgan@Example.com", billing_street="Original address")
        second = self.create(contact_email="morgan@example.com", contact_full_name="Another submitted name",
                             billing_street="Different address", company="Different company")
        self.assertEqual(first.client_contact_id, second.client_contact_id)
        self.assertNotEqual(first.project_id, second.project_id)
        self.assertNotEqual(first.queue_order, second.queue_order)
        contact = second.client_contact
        self.assertEqual(contact.full_name, "Morgan Lee")
        self.assertEqual(contact.client.billing_street, "Original address")
        self.assertEqual(second.contact_full_name, "Another submitted name")
        self.assertEqual(Submission.objects.count(), 2)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_same_name_phone_company_or_address_only_suggests_never_merges(self):
        first = self.create(company="Example Builders")
        second = self.create(company="Example Builders", contact_email="other@example.com")
        self.assertNotEqual(first.client_contact.client_id, second.client_contact.client_id)
        self.assertIn(second.client_contact.client, possible_matches(first.client_contact.client))

    def test_homeowners_do_not_group_and_email_aliases_remain_distinct(self):
        first = self.create(contact_email="alex+one@gmail.com", contact_full_name="Alex A", contact_phone="5085550101")
        second = self.create(contact_email="alex@gmail.com", contact_full_name="Alex B", contact_phone="5085550102")
        self.assertNotEqual(first.client_contact.client_id, second.client_contact.client_id)
        self.assertEqual(list(possible_matches(first.client_contact.client)), [])

    def test_submission_retry_cannot_duplicate_client_or_contact(self):
        for _ in range(2):
            create_work(data=work_data(), actor=None, idempotency_key="repeat-key")
        self.assertEqual(CRMClient.objects.count(), 1)
        self.assertEqual(ClientContact.objects.count(), 1)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_sync_handles_missing_email_and_is_idempotent_and_preserves_snapshots(self):
        for n in range(2):
            WorkItem.objects.create(reference=f"OLD-{n}", queue_order=n+1, description="Old description",
                contact_full_name="Historical client", contact_email="", contact_phone="", company="")
        self.assertEqual(sync_clients(), 2)
        self.assertEqual(sync_clients(), 0)
        self.assertEqual(ClientContact.objects.filter(email__isnull=True).count(), 2)
        self.assertEqual(CRMClient.objects.count(), 2)
        self.assertEqual(set(WorkItem.objects.values_list("contact_email", flat=True)), {""})


class EditingTests(TestCase):
    create = MatchingTests.create
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("crm-owner", "owner@example.invalid", "test-only")

    def test_profile_edit_is_private_and_stale_save_rejected(self):
        item = self.create()
        account = item.client_contact.client
        before = dict(Submission.objects.get(work_item=item).answers)
        old_version = account.version
        edit_client(client_id=account.pk, version=old_version, data={"internal_notes": "PRIVATE CRM NOTES"}, actor=self.owner)
        with self.assertRaises(EditConflict):
            edit_client(client_id=account.pk, version=old_version, data={"name": "Stale"}, actor=self.owner)
        self.assertEqual(Submission.objects.get(work_item=item).answers, before)
        self.assertEqual(len(mail.outbox), 0)

    def test_merge_and_undo_keep_requests_snapshots_positions_access_and_source_notes(self):
        first, second = self.create(), self.create(contact_email="second@example.com")
        source, target = first.client_contact.client, second.client_contact.client
        source.internal_notes = "Original private notes"
        source.save()
        before = list(WorkItem.objects.order_by("reference").values("reference", "queue_order", "contact_email", "project_id"))
        target = merge_clients(source_id=source.pk, source_version=source.version,
            target_id=target.pk, target_version=target.version, actor=self.owner)
        self.assertEqual(target.contacts.count(), 2)
        source.refresh_from_db()
        self.assertEqual(source.internal_notes, "Original private notes")
        event = target.events.get(action="Client records merged")
        restored = undo_merge(client_id=target.pk, version=target.version, event_id=event.pk, actor=self.owner)
        self.assertIsNone(restored.merged_into_id)
        self.assertEqual(restored.contacts.count(), 1)
        self.assertEqual(list(WorkItem.objects.order_by("reference").values("reference", "queue_order", "contact_email", "project_id")), before)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_merge_preview_cannot_target_itself_or_archived_clients(self):
        item = self.create()
        account = item.client_contact.client
        with self.assertRaises(ValidationError):
            merge_clients(source_id=account.pk, source_version=account.version,
                target_id=account.pk, target_version=account.version, actor=self.owner)

    def test_new_intake_after_merge_blocks_stale_undo(self):
        first, second = self.create(), self.create(contact_email="second@example.com")
        source, target = first.client_contact.client, second.client_contact.client
        target = merge_clients(source_id=source.pk, source_version=source.version,
            target_id=target.pk, target_version=target.version, actor=self.owner)
        event = target.events.get(action="Client records merged")
        self.create()
        target.refresh_from_db()
        with self.assertRaises(EditConflict):
            undo_merge(client_id=target.pk, version=target.version, event_id=event.pk, actor=self.owner)

    def test_contact_cannot_be_edited_from_another_client_or_reuse_an_email(self):
        first, second = self.create(), self.create(contact_email="second@example.com")
        account = first.client_contact.client
        with self.assertRaises(ValidationError):
            save_contact(client_id=account.pk, version=account.version, contact_id=second.client_contact_id,
                data={"full_name": "Wrong", "phone": ""}, actor=self.owner)
        with self.assertRaises(ValidationError):
            save_contact(client_id=account.pk, version=account.version, contact_id=None,
                data={"full_name": "Wrong", "phone": "", "email": "SECOND@example.com"}, actor=self.owner)
        save_contact(client_id=account.pk, version=account.version, contact_id=None,
            data={"full_name": "Another employee", "phone": "", "email": "employee@example.com"}, actor=self.owner)
        new = self.create(contact_email="employee@example.com")
        self.assertEqual(new.client_contact.client_id, account.pk)


@override_settings(WORK_QUEUE_ENABLED=True)
class CRMViewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("crm-view-owner", "owner@example.invalid", "test-only")
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.account = self.item.client_contact.client
        self.url = reverse("workqueue:client_detail", args=[self.account.pk])
        self.directory = reverse("workqueue:clients")

    def test_anonymous_clients_and_unpermitted_staff_cannot_read_or_write_crm(self):
        self.assertEqual(self.client.get(self.directory).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        user = get_user_model().objects.create_user("normal-client")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        user.is_staff = True
        user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"action": "edit"}).status_code, 403)

    def test_reader_sees_history_without_edit_access_and_pages_are_not_cacheable(self):
        reader = get_user_model().objects.create_user("reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.client.force_login(reader)
        response = self.client.get(self.url)
        self.assertContains(response, self.item.reference)
        self.assertNotContains(response, "Save client")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post(self.url, {"action": "edit"}).status_code, 403)

    def test_directory_search_counts_all_work_and_links_from_queue(self):
        second = create_work(data=work_data(project_name="Different project"), actor=self.owner)[0]
        self.client.force_login(self.owner)
        response = self.client.get(self.directory, {"q": self.item.reference, "scope": "repeat"})
        self.assertContains(response, "2 total requests")
        self.assertContains(response, self.url)
        self.assertContains(self.client.get(reverse("workqueue:queue")), self.url)
        self.assertContains(self.client.get(self.url), second.reference)

    def test_merge_requires_fresh_signed_preview_and_is_reversible_in_browser(self):
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.owner)[0].client_contact.client
        self.client.force_login(self.owner)
        data = {"action": "merge", "merge-version": self.account.version, "merge-target": other.pk}
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        data["action"] = "preview_merge"
        preview = self.client.post(self.url, data)
        self.assertContains(preview, "Confirm connection")
        data.update(action="merge", signature=preview.context["merge_signature"])
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        other.refresh_from_db()
        event = other.events.get(action="Client records merged")
        self.assertEqual(self.client.post(reverse("workqueue:client_detail", args=[other.pk]),
            {"action": "undo_merge", "version": other.version, "event_id": event.pk}).status_code, 302)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.merged_into_id)

    def test_csrf_is_required_and_html_in_notes_is_escaped(self):
        from django.test import Client
        self.client.force_login(self.owner)
        self.account.internal_notes = '<script>alert("private")</script>'
        self.account.save()
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("private")</script>')
        checked = Client(enforce_csrf_checks=True)
        checked.force_login(self.owner)
        self.assertEqual(checked.post(self.url, {"action": "edit"}).status_code, 403)


@skipUnlessDBFeature("has_select_for_update")
class CRMConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)

    def test_concurrent_submissions_share_one_contact_but_keep_two_queue_entries(self):
        def create(index):
            close_old_connections()
            try:
                return create_work(data=work_data(), actor=None, idempotency_key=f"concurrent-crm-{index}")[0].pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(create, range(2)))
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(ClientContact.objects.count(), 1)
        self.assertEqual(CRMClient.objects.count(), 1)
