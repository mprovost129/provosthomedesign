import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .crm import create_client, create_client_work, save_contact
from .models import ClientContact, ProjectAccess, QueueState, Submission, WorkItem
from .services import EditConflict, create_work
from .test_crm_manual import client_data
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True)
class ContactEmailTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("email-editor", "owner@example.invalid", "local-only")
        self.account = create_client(data=client_data(), nonce=uuid.uuid4(), actor=self.owner)[0]
        self.contact = self.account.contacts.get()
        self.client.force_login(self.owner)
        self.url = reverse("workqueue:client_detail", args=[self.account.pk])

    def save(self, **changes):
        self.account.refresh_from_db()
        data = {"full_name": self.contact.full_name, "phone": self.contact.phone, **changes}
        save_contact(client_id=self.account.pk, version=self.account.version, contact_id=self.contact.pk,
                     data=data, actor=self.owner)
        self.contact.refresh_from_db()

    def test_missing_email_fixed_and_future_public_and_manual_work_attach_to_same_client(self):
        self.save(email=" FIXED@Example.com ")
        self.assertEqual(self.contact.email, "fixed@example.com")
        auto = create_work(data=work_data(contact_email="fixed@example.com"), actor=None)[0]
        self.assertEqual(auto.client_contact_id, self.contact.pk)
        self.account.refresh_from_db()
        manual = create_client_work(client_id=self.account.pk, version=self.account.version, actor=self.owner,
            data={"kind": "new", "project_name": "Phone job", "description": "Phone request", "contact": self.contact},
            nonce=uuid.uuid4(), binding="staff-test")[0]
        self.assertEqual(manual.contact_email, "fixed@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_edit_preserves_original_work_snapshots_ownership_and_access(self):
        item = create_work(data=work_data(), actor=self.owner)[0]
        account, contact = item.client_contact.client, item.client_contact
        grant = ProjectAccess.objects.create(project=item.project, email=item.contact_email)
        submission = item.submissions.get()
        before = dict(submission.answers)
        save_contact(client_id=account.pk, version=account.version, contact_id=contact.pk,
            data={"email": "corrected@example.com", "full_name": contact.full_name, "phone": contact.phone}, actor=self.owner)
        item.refresh_from_db(); submission.refresh_from_db(); grant.refresh_from_db()
        self.assertEqual(item.contact_email, "morgan@example.com")
        self.assertEqual(submission.owner_email, "morgan@example.com")
        self.assertEqual(submission.answers, before)
        self.assertEqual(grant.email, "morgan@example.com")
        event = account.events.get(action="Contact updated")
        self.assertEqual(event.changes["before"]["email"], "morgan@example.com")
        self.assertEqual(event.changes["after"]["email"], "corrected@example.com")

    def test_duplicate_invalid_and_stale_emails_rejected_without_losing_original(self):
        create_work(data=work_data(contact_email="taken@example.com"), actor=self.owner)
        self.save(email="my@example.com")
        for value in ["TAKEN@example.com", "not-an-email"]:
            with self.assertRaises(ValidationError):
                self.save(email=value)
            self.contact.refresh_from_db()
            self.assertEqual(self.contact.email, "my@example.com")
        # Keeping this contact's email (with different capitalization) is valid.
        stale_version = self.account.version
        self.save(email="MY@example.com")
        with self.assertRaises(EditConflict):
            save_contact(client_id=self.account.pk, version=stale_version, contact_id=self.contact.pk,
                data={"email": "stale@example.com", "full_name": "Stale", "phone": ""}, actor=self.owner)
        with self.assertRaises(PermissionDenied):
            save_contact(client_id=self.account.pk, version=1, contact_id=self.contact.pk, data={}, actor=None)

    def test_omitted_email_preserved_but_explicit_blank_becomes_null(self):
        self.save(email="keep@example.com")
        self.save(phone="5085550123")
        self.assertEqual(self.contact.email, "keep@example.com")
        self.save(email="")
        self.assertIsNone(self.contact.email)
        other = create_client(data=client_data(name="Other phone client"), nonce=uuid.uuid4(), actor=self.owner)[0]
        self.assertIsNone(other.contacts.get().email)
        self.assertEqual(ClientContact.objects.filter(email__isnull=True).count(), 2)

    def test_edit_form_prefills_email_saves_and_retains_bad_input(self):
        prefix = f"contact-{self.contact.pk}"
        response = self.client.get(self.url)
        self.assertContains(response, f'name="{prefix}-email"')
        data = {"action": "contact", "contact_key": self.contact.pk, f"{prefix}-contact_id": self.contact.pk,
                f"{prefix}-version": self.account.version, f"{prefix}-full_name": "Phone client",
                f"{prefix}-phone": "", f"{prefix}-email": "bad-email"}
        response = self.client.post(self.url, data)
        self.assertContains(response, "bad-email", status_code=400)
        data[f"{prefix}-email"] = "added@example.com"
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        response = self.client.get(self.url)
        self.assertContains(response, f'name="{prefix}-email" value="added@example.com"')
        self.account.refresh_from_db()
        data[f"{prefix}-version"] = self.account.version
        del data[f"{prefix}-email"]  # older open page from before deployment
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.email, "added@example.com")


@skipUnlessDBFeature("has_select_for_update")
class EmailConcurrencyTests(TransactionTestCase):
    def test_two_staff_edits_cannot_claim_the_same_email(self):
        QueueState.objects.get_or_create(pk=1)
        actor = get_user_model().objects.create_superuser("concurrent-email", "owner@example.invalid", "local-only")
        accounts = [create_client(data=client_data(name=f"Phone client {i}"), nonce=uuid.uuid4(), actor=actor)[0]
                    for i in range(2)]
        contacts = [account.contacts.get() for account in accounts]
        def save(i):
            close_old_connections()
            try:
                save_contact(client_id=accounts[i].pk, version=accounts[i].version, contact_id=contacts[i].pk,
                    data={"email": "same@example.com", "full_name": "Phone client", "phone": ""}, actor=actor)
                return "saved"
            except ValidationError:
                return "conflict"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, range(2)))
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(ClientContact.objects.filter(email="same@example.com").count(), 1)
