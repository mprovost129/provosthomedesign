import uuid

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .crm import archive_contact, create_client_work, restore_contact, save_contact
from .models import ClientContact, ProjectAccess, Submission, WorkItem
from .services import EditConflict, create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True)
class ContactLifecycleTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser("contact-owner", "owner@example.invalid", "local-only")
        self.item = create_work(data=work_data(), actor=self.actor)[0]
        self.contact = self.item.client_contact
        self.account = self.contact.client
        self.client.force_login(self.actor)
        self.url = reverse("workqueue:client_detail", args=[self.account.pk])

    def args(self, contact=None):
        self.account.refresh_from_db()
        return dict(client_id=self.account.pk, version=self.account.version,
                    contact_id=(contact or self.contact).pk, actor=self.actor)

    def test_copy_contact_details_without_copying_jobs_files_email_or_access(self):
        self.account.refresh_from_db()
        copied = save_contact(client_id=self.account.pk, version=self.account.version, contact_id=None,
            copy_from_id=self.contact.pk, data={"full_name": self.contact.full_name, "phone": self.contact.phone, "email": ""}, actor=self.actor)
        self.assertNotEqual(copied.pk, self.contact.pk)
        self.assertIsNone(copied.email)
        self.assertEqual(copied.full_name, self.contact.full_name)
        self.assertEqual(copied.phone, self.contact.phone)
        self.assertEqual(copied.submitted_company, self.contact.submitted_company)
        self.assertEqual(copied.work_items.count(), 0)
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.account.events.get(action="Contact copied").changes["copied_from"], str(self.contact.pk))

    def test_delete_restore_keep_jobs_submitted_email_order_and_access_unchanged(self):
        grant = ProjectAccess.objects.create(project=self.item.project, email=self.contact.email)
        before = dict(self.item.submissions.get().answers)
        order = self.item.queue_order
        archived = archive_contact(**self.args())
        self.assertIsNotNone(archived.archived_at)
        self.assertIsNone(archived.email)
        self.assertEqual(archived.archived_email, "morgan@example.com")
        self.item.refresh_from_db(); grant.refresh_from_db()
        self.assertEqual(self.item.client_contact_id, self.contact.pk)
        self.assertEqual(self.item.contact_email, "morgan@example.com")
        self.assertEqual(self.item.queue_order, order)
        self.assertEqual(self.item.submissions.get().answers, before)
        self.assertEqual(grant.email, "morgan@example.com")
        self.assertContains(self.client.get(self.url), self.item.reference)
        restored = restore_contact(**self.args())
        self.assertIsNone(restored.archived_at)
        self.assertEqual(restored.email, "morgan@example.com")

    def test_deleted_email_can_be_used_again_and_conflicting_restore_requires_explicit_choice(self):
        archive_contact(**self.args())
        fresh = create_work(data=work_data(), actor=None)[0]
        self.assertNotEqual(fresh.client_contact_id, self.contact.pk)
        self.assertNotEqual(fresh.client_contact.client_id, self.account.pk)
        with self.assertRaises(ValidationError):
            restore_contact(**self.args())
        restored = restore_contact(**self.args(), restore_email=False)
        self.assertIsNone(restored.email)
        self.assertIsNone(restored.archived_at)
        fresh.client_contact.refresh_from_db()
        self.assertEqual(fresh.client_contact.email, "morgan@example.com")

    def test_deleted_contacts_not_selectable_for_manual_work_or_edits_and_other_clients_protected(self):
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.actor)[0].client_contact
        for operation in [archive_contact, restore_contact]:
            with self.assertRaises(ValidationError):
                operation(**self.args(other))
        archive_contact(**self.args())
        self.account.refresh_from_db()
        with self.assertRaises(ValidationError):
            save_contact(**self.args(), data={"full_name": "Changed", "phone": ""})
        with self.assertRaises(ValidationError):
            create_client_work(client_id=self.account.pk, version=self.account.version, actor=self.actor,
                data={"contact": self.contact, "kind": "new", "project_name": "Bad", "description": "Deleted contact"},
                nonce=uuid.uuid4(), binding="test")
        form = self.client.get(reverse("workqueue:add_client_work", args=[self.account.pk])).context["form"]
        self.assertNotIn(self.contact.pk, form.fields["contact"].queryset.values_list("pk", flat=True))

    def test_stale_or_unauthorized_delete_cannot_remove_contact(self):
        version = self.account.version
        save_contact(**self.args(), data={"full_name": "Updated name", "phone": ""})
        with self.assertRaises(EditConflict):
            archive_contact(client_id=self.account.pk, version=version, contact_id=self.contact.pk, actor=self.actor)
        with self.assertRaises(PermissionDenied):
            archive_contact(client_id=self.account.pk, version=version, contact_id=self.contact.pk, actor=None)
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.archived_at)

    def test_copy_delete_restore_through_staff_forms(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Edit contact")
        self.assertContains(response, "Delete contact")
        prefix = f"copy-{self.contact.pk}"
        self.assertContains(response, f'name="{prefix}-email"')
        data = {"action": "copy_contact", "contact_key": self.contact.pk,
            f"{prefix}-version": self.account.version, f"{prefix}-full_name": "Second contact",
            f"{prefix}-phone": self.contact.phone, f"{prefix}-email": ""}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.post(self.url, data).status_code, 409)
        copied = self.account.contacts.get(full_name="Second contact")
        self.account.refresh_from_db()
        state = {"action": "delete_contact", "version": self.account.version, "contact_id": copied.pk}
        self.assertEqual(self.client.post(self.url, state).status_code, 302)
        response = self.client.get(self.url)
        self.assertContains(response, "Deleted contacts (1)")
        self.assertContains(response, "Restore contact")
        self.account.refresh_from_db()
        self.assertEqual(self.client.post(self.url, {**state, "action": "restore_contact", "version": self.account.version}).status_code, 302)
        copied.refresh_from_db()
        self.assertIsNone(copied.archived_at)

    def test_copy_cannot_borrow_a_contact_or_reuse_email(self):
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.actor)[0].client_contact
        for source in [other, self.contact]:
            with self.assertRaises(ValidationError):
                save_contact(client_id=self.account.pk, version=self.account.version, contact_id=None,
                    copy_from_id=source.pk, data={"full_name": "Copy", "phone": "", "email": source.email}, actor=self.actor)
        self.assertEqual(ClientContact.objects.count(), 2)
