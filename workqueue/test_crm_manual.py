import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .crm import create_client, create_client_work, merge_clients
from .models import Attachment, Client, ClientContact, PendingUpload, ProjectAccess, Submission, WorkItem
from .services import EditConflict, create_work
from .tests import work_data


def client_data(**changes):
    values = {"name": "Phone client", "kind": "individual", "full_name": "", "phone": "", "email": "",
              "billing_street": "", "billing_city": "", "billing_state": "", "billing_zip": "", "internal_notes": ""}
    values.update(changes)
    return values


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
class ManualCRMTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("manual-owner", "owner@example.invalid", "local-only")
        self.account = create_client(data=client_data(), nonce=uuid.uuid4(), actor=self.owner)[0]
        self.contact = self.account.contacts.get()
        self.client.force_login(self.owner)

    def add_work(self, **changes):
        self.account.refresh_from_db()
        values = {"kind": "new", "project": None, "project_name": "Phone project", "description": "Draw an addition",
                  "contact": self.contact, "requested_deadline": None, "upload_ids": ""}
        values.update(changes)
        return create_client_work(client_id=self.account.pk, version=self.account.version,
            data=values, nonce=uuid.uuid4(), binding="staff-binding", actor=self.owner)[0]

    def test_phone_only_client_retry_and_duplicate_email_reuse(self):
        nonce = uuid.uuid4()
        first, created = create_client(data=client_data(name="Other client"), nonce=nonce, actor=self.owner)
        second, repeated = create_client(data=client_data(name="Changed on retry"), nonce=nonce, actor=self.owner)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, second.pk)
        emailed = create_client(data=client_data(email="EXISTING@example.com"), nonce=uuid.uuid4(), actor=self.owner)[0]
        reused, created = create_client(data=client_data(name="Do not overwrite", email="existing@example.com"), nonce=uuid.uuid4(), actor=self.owner)
        self.assertFalse(created)
        self.assertEqual(reused.pk, emailed.pk)
        self.assertNotEqual(reused.name, "Do not overwrite")

    def test_staff_unknown_contact_fields_allowed_without_weakening_public_validation(self):
        item = self.add_work()
        self.assertEqual(item.client_contact_id, self.contact.pk)
        self.assertEqual(item.contact_email, "")
        self.assertEqual(item.contact_phone, "")
        self.assertEqual(item.contact_full_name, self.account.name)
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        with self.assertRaises(ValidationError):
            create_work(data=work_data(contact_email="", contact_phone=""), actor=None)
        with self.assertRaises(PermissionDenied):
            create_work(data=work_data(), actor=None, staff_contact=self.contact)

    def test_manual_update_is_linked_and_receives_its_own_queue_position(self):
        first = self.add_work()
        second = self.add_work(kind="update", project=first.project)
        self.assertEqual(second.project_id, first.project_id)
        self.assertEqual(second.client_contact_id, first.client_contact_id)
        self.assertEqual(second.submission_position, first.submission_position + 1)
        self.assertNotEqual(second.reference, first.reference)

    def test_other_client_contact_and_project_rejected_atomically(self):
        foreign = create_work(data=work_data(), actor=self.owner)[0]
        for values in [{"contact": foreign.client_contact}, {"project": foreign.project}]:
            with self.assertRaises(ValidationError):
                self.add_work(**values)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_retry_after_version_change_never_duplicates_job_or_file(self):
        nonce = uuid.uuid4()
        upload = PendingUpload.objects.create(intake_nonce=nonce, session_digest="staff-binding", state="ready",
            original_name="plans.pdf", storage_key="intake/test/plans.pdf", content_type="application/pdf", size_bytes=123,
            expires_at=timezone.now()+timedelta(hours=1))
        data = {"kind": "new", "project_name": "Test project", "description": "Plans supplied by phone",
                "contact": self.contact, "upload_ids": str(upload.pk)}
        args = dict(client_id=self.account.pk, version=self.account.version, data=data,
                    nonce=nonce, binding="staff-binding", actor=self.owner)
        first, created = create_client_work(**args)
        second, repeated = create_client_work(**args)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Attachment.objects.count(), 1)
        upload.refresh_from_db()
        self.assertEqual(upload.state, "attached")

    def test_uploads_cannot_be_borrowed_from_other_sessions_or_expired(self):
        upload = PendingUpload.objects.create(intake_nonce=uuid.uuid4(), session_digest="other-session", state="ready",
            original_name="other.pdf", storage_key="intake/test/other.pdf", size_bytes=123,
            expires_at=timezone.now()+timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.add_work(upload_ids=str(upload.pk))
        nonce = uuid.uuid4()
        upload.intake_nonce, upload.session_digest = nonce, "staff-binding"
        upload.expires_at = timezone.now()-timedelta(seconds=1)
        upload.save()
        with self.assertRaises(ValidationError):
            create_client_work(client_id=self.account.pk, version=self.account.version,
                data={"kind": "new", "contact": self.contact, "description": "Expired file",
                      "project_name": "Expired project", "upload_ids": str(upload.pk)},
                nonce=nonce, binding="staff-binding", actor=self.owner)
        self.assertEqual(WorkItem.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_stale_client_or_merge_rejects_new_job(self):
        stale_version = self.account.version
        self.add_work()
        with self.assertRaises(EditConflict):
            create_client_work(client_id=self.account.pk, version=stale_version, actor=self.owner,
                data={"contact": self.contact}, nonce=uuid.uuid4(), binding="staff-binding")

    def test_client_and_work_forms_save_and_survive_validation_retry(self):
        url = reverse("workqueue:add_client")
        token = self.client.get(url).context["form"].initial["intake_token"]
        post = {**client_data(name="Manual UI client"), "intake_token": token}
        invalid = self.client.post(url, {**post, "name": ""})
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, 'Manual UI client', status_code=400, count=0)
        result = self.client.post(url, post)
        self.assertEqual(result.status_code, 302)
        account = Client.objects.get(name="Manual UI client")
        work_url = reverse("workqueue:add_client_work", args=[account.pk])
        form = self.client.get(work_url).context["form"]
        data = {"intake_token": form.initial["intake_token"], "version": account.version,
            "contact": str(account.contacts.get().pk), "kind": "new", "project_name": "Manual UI project", "description": "Work from a call"}
        invalid = self.client.post(work_url, {**data, "description": ""})
        self.assertContains(invalid, "Manual UI project", status_code=400)
        response = self.client.post(work_url, data)
        self.assertEqual(response.status_code, 302)
        item = WorkItem.objects.get(project_name="Manual UI project")
        self.assertEqual(item.client_contact.client_id, account.pk)
        self.assertEqual(self.client.post(work_url, data).status_code, 302)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_projects_include_client_and_manual_jobs_and_project_filter_preserves_counts(self):
        # One manually added contact can later submit with the same email.
        self.contact.email = "manual@example.com"
        self.contact.save()
        manual = self.add_work()
        submitted = create_work(data=work_data(contact_email=self.contact.email, project=manual.project,
            kind="update", source="Website"), actor=None)[0]
        submission = submitted.submissions.get()
        submission.channel = "website"
        submission.save()
        separate = self.add_work(project_name="Separate project")
        url = reverse("workqueue:client_detail", args=[self.account.pk])
        response = self.client.get(url)
        self.assertEqual(response.context["request_count"], 3)
        self.assertEqual(len(response.context["projects"]), 2)
        self.assertContains(response, "Added by staff")
        self.assertContains(response, "Client submission")
        response = self.client.get(url, {"project": str(manual.project_id)})
        self.assertEqual(response.context["request_count"], 3)
        self.assertEqual(set(x.pk for x in response.context["page"]), {manual.pk, submitted.pk})
        self.assertNotIn(separate.pk, {x.pk for x in response.context["page"]})

    def test_missing_or_read_only_staff_permissions_cannot_add_entries(self):
        urls = [reverse("workqueue:add_client"), reverse("workqueue:add_client_work", args=[self.account.pk])]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user("readonly-crm", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_workitem", content_type__app_label="workqueue"))
        self.client.force_login(user)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("workqueue:clients")), "Add client")
