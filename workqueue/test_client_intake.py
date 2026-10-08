import hashlib
import re
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import consume_email_token, mint_email_token, resolve_project
from .client_forms import ClientIntakeForm
from .models import (Attachment, EmailAccessToken, NotificationDelivery, PendingUpload,
                     ProjectAccess, QueueState, Status, Submission, WorkItem, WorkProject)
from .notifications import deliver_pending
from .services import create_work, edit_work
from .uploads import checked_name, direct_upload_policy, validate_content


def new_answers(**changes):
    answers = {"terms_accepted": "on", "kind": "new", "contact_full_name": "Alex Example", "company": "Homeowner",
        "contact_email": "alex@example.invalid", "contact_phone": "508-555-0100 ext 3",
        "billing_street": "1 Billing Road", "billing_city": "Rehoboth", "billing_state": "MA", "billing_zip": "02769",
        "project_street": "4 Project Road Unit 2", "project_city": "Swansea", "project_state": "MA", "project_zip": "02777",
        "service_needed": "Addition or renovation", "new_description": "Enlarge the kitchen and coordinate framing.",
        "project_name": "Kitchen addition"}
    answers.update(changes)
    return answers


def valid_pdf(name="sketch.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj << >> endobj\n%%EOF\n", content_type="application/pdf")


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, RECAPTCHA_ENTERPRISE_API_KEY="", RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY="", WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True,
    INTAKE_DIRECT_UPLOADS=False, INTAKE_OWNER_EMAIL="owner@example.invalid",
    INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ClientFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.files = TemporaryDirectory()
        self.storage_settings = override_settings(STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "intake_private": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                               "OPTIONS": {"location": self.files.name, "base_url": None}}})
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)
        self.addCleanup(self.files.cleanup)
        response = self.client.get(reverse("workqueue:submit"))
        self.token = response.context["form"].initial["intake_token"]

    def submit(self, **changes):
        return self.client.post(reverse("workqueue:submit"), new_answers(intake_token=self.token, **changes))

    def verify(self, email="alex@example.invalid", reference=""):
        record, raw = mint_email_token(email, reference)
        return self.client.post(reverse("workqueue:confirm_access"), {"token": raw})

    def test_new_intake_requires_all_twelve_contact_and_address_fields(self):
        names = ["contact_full_name", "company", "contact_email", "contact_phone", "billing_street", "billing_city",
                 "billing_state", "billing_zip", "project_street", "project_city", "project_state", "project_zip"]
        for name in names:
            form = ClientIntakeForm(new_answers(**{name: "", "intake_token": self.token}))
            self.assertFalse(form.is_valid(), name)
            self.assertIn(name, form.errors)
        self.assertEqual(WorkItem.objects.count(), 0)

    def test_new_submission_goes_directly_to_queue_with_snapshot_and_two_notices(self):
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        item = WorkItem.objects.get()
        self.assertEqual(item.status, Status.NEW)
        self.assertEqual(item.submission_position, 1)
        self.assertEqual(item.billing_street, "1 Billing Road")
        self.assertEqual(item.project_street, "4 Project Road Unit 2")
        self.assertEqual(item.project_zip, "02777")
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(Submission.objects.get().channel, "website")
        self.assertFalse(Submission.objects.get().answers.get("intake_token"))
        confirmation = self.client.get(response.url)
        self.assertContains(confirmation, item.reference)
        self.assertContains(confirmation, "Position when submitted")

    def test_submission_retry_does_not_duplicate_work_or_email(self):
        self.submit()
        self.submit(new_description="Accidental double click")
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(WorkItem.objects.get().description, "Enlarge the kitchen and coordinate framing.")

    def test_anonymous_confirmation_is_bound_to_original_browser(self):
        from django.test import Client
        response = self.submit()
        self.assertEqual(Client().get(response.url).status_code, 404)

    def test_guessed_reference_does_not_reveal_client_records(self):
        self.submit()
        response = self.client.get(reverse("workqueue:tracking"), {"reference": WorkItem.objects.get().reference})
        self.assertContains(response, "Get a secure sign-in link")
        self.assertNotContains(response, "Kitchen addition")

    def test_email_receipt_and_confirmation_share_submission_snapshot(self):
        self.submit()
        item = WorkItem.objects.get()
        notification = NotificationDelivery.objects.get(recipient_kind="client")
        self.assertIn("Position when submitted: 1", notification.body)
        create_work(data={"contact_full_name": "Another Client", "company": "Homeowner",
            "contact_email": "another@example.invalid", "contact_phone": "5550100", "description": "Another request"}, actor=None)
        self.assertEqual(item.submission_position, 1)
        deliver_pending()
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Position when submitted: 1", mail.outbox[0].body)
        self.assertTrue(NotificationDelivery.objects.filter(state="sent").count() == 2)
        self.assertFalse(NotificationDelivery.objects.exclude(body="").exists())

    def test_update_needs_no_repeated_billing_or_project_address_form(self):
        form = ClientIntakeForm({"terms_accepted": "on", "kind": "update", "contact_full_name": "Alex Example", "company": "Homeowner",
            "contact_email": "alex@example.invalid", "contact_phone": "508-555-0100", "intake_token": self.token,
            "project_context": "Kitchen addition", "update_description": "Change the window sizes", "categories": ["Revision request"]})
        self.assertTrue(form.is_valid(), form.errors)

    def test_anonymous_update_queues_even_with_unknown_reference(self):
        response = self.client.post(reverse("workqueue:submit"), {"terms_accepted": "on", "kind": "update", "contact_full_name": "Alex Example",
            "company": "Homeowner", "contact_email": "alex@example.invalid", "contact_phone": "508-555-0100",
            "intake_token": self.token, "project_reference": "PHD-12345", "update_description": "Window changes",
            "categories": ["Revision request", "Answers or corrections"]})
        self.assertEqual(response.status_code, 302)
        item = WorkItem.objects.get()
        self.assertTrue(item.needs_project_link)
        self.assertEqual(item.reference, "PHD-00001")
        self.assertEqual(Submission.objects.get().answers["What are you sending?"], ["Revision request", "Answers or corrections"])

    def test_verified_update_uses_project_name_without_id_and_own_position(self):
        self.submit()
        original = WorkItem.objects.get()
        self.verify()
        response = self.client.get(reverse("workqueue:submit"))
        token = response.context["form"].initial["intake_token"]
        response = self.client.post(reverse("workqueue:submit"), {"terms_accepted": "on", "kind": "update", "contact_full_name": "Alex Example",
            "company": "Homeowner", "contact_email": "alex@example.invalid", "contact_phone": "508-555-0100",
            "intake_token": token, "project_context": "  kitchen ADDITION  ", "update_description": "Window changes",
            "categories": ["Revision request"]})
        self.assertEqual(response.status_code, 302)
        update = WorkItem.objects.exclude(pk=original.pk).get()
        self.assertEqual(update.project_id, original.project_id)
        self.assertEqual(update.submission_position, 2)
        self.assertNotEqual(update.reference, original.reference)

    def test_changed_contact_email_cannot_use_signed_in_project_access(self):
        self.submit()
        original = WorkItem.objects.get()
        self.verify()
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        self.client.post(reverse("workqueue:submit"), {"terms_accepted": "on", "kind": "update", "contact_full_name": "Other", "company": "Homeowner",
            "contact_email": "other@example.invalid", "contact_phone": "508-555-0100", "intake_token": self.token,
            "project_reference": original.reference, "update_description": "Window changes", "categories": ["Revision request"]})
        self.assertIsNone(WorkItem.objects.exclude(pk=original.pk).get().project_id)

    def test_email_verification_is_single_use_and_get_does_not_consume_it(self):
        record, raw = mint_email_token("alex@example.invalid")
        response = self.client.get(reverse("workqueue:confirm_access"))
        record.refresh_from_db()
        self.assertIsNone(record.used_at)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(reverse("workqueue:confirm_access"), {"token": raw}).status_code, 302)
        self.assertEqual(self.client.post(reverse("workqueue:confirm_access"), {"token": raw}).status_code, 400)

    def test_expired_email_link_cannot_authenticate(self):
        record, raw = mint_email_token("alex@example.invalid")
        EmailAccessToken.objects.filter(pk=record.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post(reverse("workqueue:confirm_access"), {"token": raw}).status_code, 400)
        self.assertNotIn("queue_verified_email", self.client.session)

    def test_unverified_email_text_does_not_grant_project_access(self):
        self.submit()
        self.assertFalse(ProjectAccess.objects.exists())
        self.verify()
        self.assertTrue(ProjectAccess.objects.filter(email="alex@example.invalid").exists())

    def test_unknown_and_known_email_requests_have_same_response(self):
        self.submit()
        first = self.client.post(reverse("workqueue:tracking"), {"email": "alex@example.invalid", "reference": "PHD-00001"})
        second = self.client.post(reverse("workqueue:tracking"), {"email": "unknown@example.invalid", "reference": "PHD-99999"})
        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.context["sent"], second.context["sent"])
        self.assertNotContains(second, "Kitchen addition")

    def test_tracking_positions_change_without_exposing_internal_notes(self):
        self.submit()
        first = WorkItem.objects.get()
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        self.submit(project_name="Second project")
        second = WorkItem.objects.exclude(pk=first.pk).get()
        edit_work(item_id=first.pk, version=1, actor=None, data={"status": Status.COMPLETED, "internal_notes": "PRIVATE NOTE"})
        self.verify()
        response = self.client.get(reverse("workqueue:tracking"))
        self.assertNotContains(response, "PRIVATE NOTE")
        found = {item.pk: item for item in response.context["items"]}
        self.assertEqual(found[second.pk].position, 1)
        self.assertEqual(second.submission_position, 2)
        self.assertIsNone(found[first.pk].position)

    def test_client_cannot_see_another_clients_records(self):
        self.submit()
        create_work(data={"contact_full_name": "Private Other", "company": "Secret Company",
            "contact_email": "other@example.invalid", "contact_phone": "5550100", "description": "SECRET OTHER REQUEST"}, actor=None)
        self.verify()
        response = self.client.get(reverse("workqueue:tracking"))
        self.assertNotContains(response, "SECRET OTHER REQUEST")
        self.assertNotContains(response, "Secret Company")

    def test_client_cannot_search_or_select_unauthorized_project(self):
        other = WorkProject.objects.create(name="Private Project")
        form = ClientIntakeForm({"terms_accepted": "on", "kind": "update", "contact_full_name": "Alex", "company": "Homeowner",
            "contact_email": "alex@example.invalid", "contact_phone": "5550100", "intake_token": self.token,
            "project": str(other.pk), "update_description": "Change", "categories": ["Other"]}, email="alex@example.invalid")
        self.assertFalse(form.is_valid())
        self.assertIn("project", form.errors)
        self.assertIsNone(resolve_project("alex@example.invalid", context="Private Project")[0])

    def test_name_and_address_ambiguity_does_not_auto_link(self):
        first = WorkProject.objects.create(name="Same name", street="1 Road", city="Town", state="MA", zip_code="02769")
        second = WorkProject.objects.create(name="Same name", street="1 Road Unit 2", city="Town", state="MA", zip_code="02769")
        for project in [first, second]:
            ProjectAccess.objects.create(project=project, email="alex@example.invalid")
        self.assertIsNone(resolve_project("alex@example.invalid", context="same NAME")[0])
        self.assertEqual(resolve_project("alex@example.invalid", context="1 road unit 2 town MA 02769")[0], second)
        self.assertIsNone(resolve_project("alex@example.invalid", context="1 road")[0])

    def test_revoked_project_access_remains_revoked_after_signin(self):
        self.submit()
        self.verify()
        ProjectAccess.objects.update(revoked_at=timezone.now())
        self.verify()
        response = self.client.get(reverse("workqueue:tracking"))
        self.assertEqual(len(response.context["items"]), 0)
        self.assertTrue(ProjectAccess.objects.get().revoked_at)

    def test_unlinked_update_can_connect_after_receipt_verification(self):
        self.submit()
        original = WorkItem.objects.get()
        self.verify()
        self.client.post(reverse("workqueue:client_logout"))
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        self.client.post(reverse("workqueue:submit"), {"terms_accepted": "on", "kind": "update", "contact_full_name": "Alex", "company": "Homeowner",
            "contact_email": "alex@example.invalid", "contact_phone": "5550100", "intake_token": self.token,
            "project_context": "Kitchen addition", "update_description": "A new sketch", "categories": ["Photos or sketches"]})
        update = WorkItem.objects.exclude(pk=original.pk).get()
        self.assertIsNone(update.project_id)
        self.verify()
        update.refresh_from_db()
        self.assertEqual(update.project_id, original.project_id)
        self.assertEqual(update.submission_position, 2)

    def test_client_email_and_ip_rate_limits_stop_repeated_signin_mail(self):
        for _ in range(8):
            response = self.client.post(reverse("workqueue:tracking"), {"email": "alex@example.invalid"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(NotificationDelivery.objects.filter(recipient_kind="signin").count(), 5)

    def test_copy_same_address_does_not_overwrite_explicit_project_edit(self):
        form = ClientIntakeForm(new_answers(intake_token=self.token, same_address="on", project_city="Different Town"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["project_city"], "Different Town")

    def test_valid_picture_is_accepted_with_real_image_contents(self):
        from PIL import Image
        content = BytesIO()
        Image.new("RGB", (20, 20), "white").save(content, "PNG")
        response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token,
            "file": SimpleUploadedFile("sketch.png", content.getvalue(), content_type="image/png")})
        self.assertEqual(response.status_code, 200)

    def test_other_verified_client_cannot_download_project_file(self):
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        self.submit(upload_ids=upload["id"], categories=["Plans or survey"])
        self.verify(email="other@example.invalid")
        self.assertEqual(self.client.get(reverse("workqueue:download", args=[Attachment.objects.get().pk])).status_code, 404)

    def test_intake_token_from_another_browser_is_rejected(self):
        from django.test import Client
        response = Client().post(reverse("workqueue:submit"), new_answers(intake_token=self.token))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkItem.objects.count(), 0)

    def test_session_rotation_does_not_lose_an_in_progress_intake(self):
        self.verify()
        response = self.submit()
        self.assertEqual(response.status_code, 302)

    def test_upload_is_private_and_preserved_on_form_validation_error(self):
        response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()})
        self.assertEqual(response.status_code, 200)
        upload_id = response.json()["id"]
        response = self.submit(contact_phone="", upload_ids=upload_id, categories=["Plans or survey", "Photos or sketches"])
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "sketch.pdf", status_code=400)
        self.assertEqual(PendingUpload.objects.get().state, "ready")
        self.assertEqual(WorkItem.objects.count(), 0)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_successful_upload_is_sealed_and_authorized_download_only(self):
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        self.submit(upload_ids=upload["id"], categories=["Plans or survey", "Photos or sketches"])
        attachment = Attachment.objects.get()
        pending = PendingUpload.objects.get()
        self.assertNotEqual(pending.storage_key, pending.upload_key)
        self.assertEqual(pending.state, "attached")
        self.assertEqual(attachment.categories, ["Plans or survey", "Photos or sketches"])
        url = reverse("workqueue:download", args=[attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.verify()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        # Consume through Django's test-client wrapper so request-finished
        # cleanup does not close the surrounding PostgreSQL test transaction.
        self.assertEqual(b"".join(response.streaming_content), valid_pdf().read())

    def test_uploaded_ids_from_another_browser_cannot_attach(self):
        from django.test import Client
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        other = Client()
        token = other.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        response = other.post(reverse("workqueue:submit"), new_answers(intake_token=token, upload_ids=upload["id"], categories=["Plans or survey"]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkItem.objects.count(), 0)

    def test_file_count_size_and_signature_are_validated(self):
        with self.assertRaises(ValidationError):
            checked_name("oversized.pdf", 100 * 1024 * 1024 + 1)
        checked_name("exact-limit.pdf", 100 * 1024 * 1024)
        response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token,
            "file": SimpleUploadedFile("fake.pdf", b"<script>evil</script>", content_type="application/pdf")})
        self.assertEqual(response.status_code, 400)
        for number in range(10):
            response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf(f"{number}.pdf")})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf("extra.pdf")})
        self.assertEqual(response.status_code, 400)

    def test_incomplete_upload_cannot_create_work(self):
        from .access import read_intake_token, session_digest
        request = self.client.get(reverse("workqueue:submit")).wsgi_request
        from .uploads import reserve_upload
        upload = reserve_upload(nonce=read_intake_token(request, self.token), binding=session_digest(request), name="pending.pdf", size=20)
        response = self.submit(upload_ids=str(upload.pk), categories=["Plans or survey"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkItem.objects.count(), 0)

    def test_removed_upload_frees_a_slot_and_cannot_be_submitted(self):
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        response = self.client.post(reverse("workqueue:upload_action", args=[upload["id"]]), {"intake_token": self.token, "action": "remove"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PendingUpload.objects.get().state, "failed")
        self.assertEqual(self.submit(upload_ids=upload["id"], categories=["Plans or survey"]).status_code, 400)

    def test_cleanup_keeps_submitted_files_and_removes_expired_unsubmitted_files(self):
        first = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf("keep.pdf")}).json()
        second = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf("expire.pdf")}).json()
        expired_key = PendingUpload.objects.get(pk=second["id"]).storage_key
        self.submit(upload_ids=first["id"], categories=["Plans or survey"])
        PendingUpload.objects.update(created_at=timezone.now()-timedelta(days=2), expires_at=timezone.now()-timedelta(days=1))
        call_command("cleanup_queue_uploads")
        from .uploads import private_storage
        self.assertTrue(private_storage().exists(Attachment.objects.get().storage_key))
        self.assertFalse(private_storage().exists(expired_key))

    def test_wrong_browser_cannot_remove_an_upload(self):
        from django.test import Client
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        other = Client()
        token = other.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        response = other.post(reverse("workqueue:upload_action", args=[upload["id"]]), {"intake_token": token, "action": "remove"})
        self.assertEqual(response.status_code, 404)

    def test_cleanup_waits_until_last_possible_upload_permission_has_expired(self):
        upload = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf()}).json()
        record = PendingUpload.objects.get(pk=upload["id"])
        record.created_at = timezone.now()-timedelta(days=2)
        record.expires_at = timezone.now()-timedelta(minutes=10)
        record.save()
        key = record.storage_key
        call_command("cleanup_queue_uploads")
        from .uploads import private_storage
        self.assertTrue(private_storage().exists(key))
        record.expires_at = timezone.now()-timedelta(minutes=21)
        record.save()
        call_command("cleanup_queue_uploads")
        self.assertFalse(private_storage().exists(key))

    def test_cleanup_handles_multiple_expired_files_without_colliding_metadata_keys(self):
        for name in ["first.pdf", "second.pdf"]:
            self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf(name)})
        PendingUpload.objects.update(expires_at=timezone.now()-timedelta(days=1))
        call_command("cleanup_queue_uploads")
        self.assertEqual(PendingUpload.objects.filter(state="failed").count(), 2)
        self.assertEqual(len(set(PendingUpload.objects.values_list("storage_key", flat=True))), 2)

    def test_hidden_inapplicable_fields_do_not_override_update(self):
        data = {**new_answers(), "kind": "update", "intake_token": self.token, "categories": ["Other"],
                "project_context": "Unknown update", "update_description": "Correct update"}
        response = self.client.post(reverse("workqueue:submit"), data)
        self.assertEqual(response.status_code, 302)
        item = WorkItem.objects.get()
        self.assertEqual(item.billing_street, "")
        self.assertEqual(item.description, "Correct update")
        self.assertEqual(item.service_needed, "")

    def test_http_links_and_honeypot_are_rejected(self):
        self.assertEqual(self.submit(file_link="http://example.com/plans").status_code, 400)
        self.assertEqual(self.submit(website="spam").status_code, 400)
        self.assertEqual(WorkItem.objects.count(), 0)

    def test_csrf_is_required_for_intake_upload_and_signin_consumption(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        for url in [reverse("workqueue:submit"), reverse("workqueue:start_upload"), reverse("workqueue:confirm_access")]:
            self.assertEqual(client.post(url, {}).status_code, 403)

    def test_same_origin_browser_submission_succeeds_and_null_origin_is_rejected(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("workqueue:submit"))
        self.assertContains(response, 'name="referrer" content="same-origin"')
        token = response.context["form"].initial["intake_token"]
        data = new_answers(intake_token=token, csrfmiddlewaretoken=client.cookies["csrftoken"].value)
        self.assertEqual(client.post(reverse("workqueue:submit"), data, HTTP_ORIGIN="null").status_code, 403)
        self.assertEqual(client.post(reverse("workqueue:submit"), data, HTTP_ORIGIN="http://testserver").status_code, 302)

    def test_upload_retry_with_same_uuid_reuses_sealed_file_and_ticket(self):
        import uuid
        upload_id = str(uuid.uuid4())
        first = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token,
            "upload_id": upload_id, "file": valid_pdf()})
        upload = PendingUpload.objects.get()
        key = upload.storage_key
        second = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token,
            "upload_id": upload_id, "file": valid_pdf()})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(PendingUpload.objects.count(), 1)
        upload.refresh_from_db()
        self.assertEqual(upload.storage_key, key)
        self.assertNotEqual(upload.storage_key, upload.upload_key)
        self.submit(upload_ids=upload_id, categories=["Plans or survey"])
        self.assertEqual(Attachment.objects.get().storage_key, key)

    def test_staff_access_control_is_audited_and_requires_separate_permission(self):
        from django.contrib.auth.models import Permission
        self.submit()
        item = WorkItem.objects.get()
        staff = get_user_model().objects.create_user("limited-staff", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_workitem", content_type__app_label="workqueue"))
        self.client.force_login(staff)
        data = {"action": "access_grant", "item_id": str(item.pk), "version": item.version,
                "access_email": "designer@example.invalid"}
        self.assertEqual(self.client.post(reverse("workqueue:queue"), data).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="change_projectaccess", content_type__app_label="workqueue"))
        self.assertEqual(self.client.post(reverse("workqueue:queue"), data).status_code, 302)
        access = ProjectAccess.objects.get(email="designer@example.invalid")
        self.assertIsNone(access.revoked_at)
        item.refresh_from_db()
        self.assertEqual(self.client.post(reverse("workqueue:queue"), {**data, "action": "access_revoke", "version": item.version}).status_code, 302)
        access.refresh_from_db()
        self.assertIsNotNone(access.revoked_at)
        self.assertTrue(item.audit_events.filter(actor=staff).exists())

    def test_unknown_email_retry_requires_provider_confirmation(self):
        self.submit()
        item = WorkItem.objects.get()
        notice = NotificationDelivery.objects.get(recipient_kind="owner")
        notice.state = "unknown"
        notice.save()
        staff = get_user_model().objects.create_superuser("owner", "owner@example.invalid", "test-only")
        self.client.force_login(staff)
        data = {"action": "retry_notice", "item_id": str(item.pk), "version": item.version, "notice_id": notice.pk}
        self.assertEqual(self.client.post(reverse("workqueue:queue"), data).status_code, 400)
        notice.refresh_from_db()
        self.assertEqual(notice.state, "unknown")
        self.assertEqual(self.client.post(reverse("workqueue:queue"), {**data, "not_sent_confirmed": "yes"}).status_code, 302)
        notice.refresh_from_db()
        self.assertEqual(notice.state, "pending")

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_public_paths_are_disabled_in_production_until_cutover(self):
        for name in ["submit", "tracking", "confirm_access"]:
            self.assertEqual(self.client.get(reverse("workqueue:" + name)).status_code, 404)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OutboxTests(TestCase):
    def setUp(self):
        self.notice = NotificationDelivery.objects.create(recipient_kind="signin", recipient="alex@example.invalid",
            subject="Sign in", body="Private sign-in link", provider_reference="test-message-id")

    def test_email_failure_does_not_remove_saved_work_or_auto_retry_unknown(self):
        item = create_work(data={"contact_full_name": "Alex", "company": "Homeowner", "contact_email": "alex@example.invalid",
            "contact_phone": "5550100", "description": "Saved work"}, actor=None)[0]
        with patch("workqueue.notifications.EmailMessage.send", side_effect=TimeoutError("Provider might have accepted mail")):
            deliver_pending()
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.state, "unknown")
        self.assertTrue(WorkItem.objects.filter(pk=item.pk).exists())
        with patch("workqueue.notifications.EmailMessage.send") as sender:
            self.assertEqual(deliver_pending(), 0)
            sender.assert_not_called()

    def test_stale_sending_notice_is_held_for_reconciliation(self):
        self.notice.state = "sending"
        self.notice.last_attempt_at = timezone.now()-timedelta(hours=1)
        self.notice.save()
        deliver_pending()
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.state, "unknown")
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)

    def test_success_is_not_resent(self):
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
    def test_delayed_email_gets_a_fresh_single_use_link(self):
        from .notifications import access_url
        record, raw = mint_email_token(self.notice.recipient, "PHD-00001")
        record.expires_at = timezone.now()-timedelta(hours=1)
        record.save()
        self.notice.body = access_url(raw)
        self.notice.save()
        deliver_pending()
        delivered_raw = re.search(r"#token=([A-Za-z0-9_-]+)", mail.outbox[0].body)[1]
        self.assertNotEqual(raw, delivered_raw)
        self.assertEqual(consume_email_token(delivered_raw).reference, "PHD-00001")


class UploadValidationTests(TestCase):
    def test_direct_upload_seals_only_the_exact_validated_object_version(self):
        from .uploads import finish_upload
        content = b"%PDF-1.4\n%%EOF\n"
        storage = Mock()
        storage.bucket_name = "private-intake-test"
        storage._normalize_name.side_effect = lambda key: "intake/" + key
        client = storage.connection.meta.client
        client.head_object.return_value = {"ContentLength": len(content), "ETag": '"version-a"'}
        client.get_object.return_value = {"Body": BytesIO(content)}
        upload = Mock(upload_key="staged/random.pdf", sealed_key="ready/sealed.pdf",
                      original_name="plan.pdf", size_bytes=len(content))
        with patch("workqueue.uploads.private_storage", return_value=storage):
            finish_upload(upload)
        self.assertEqual(client.get_object.call_args.kwargs["IfMatch"], '"version-a"')
        self.assertEqual(client.copy_object.call_args.kwargs["CopySourceIfMatch"], '"version-a"')
        self.assertEqual(client.copy_object.call_args.kwargs["Key"], "intake/ready/sealed.pdf")
        self.assertEqual(upload.storage_key, "ready/sealed.pdf")

    def test_document_macros_and_extension_mismatch_are_rejected(self):
        import zipfile
        file = BytesIO()
        with zipfile.ZipFile(file, "w") as archive:
            archive.writestr("[Content_Types].xml", "types")
            archive.writestr("word/document.xml", "document")
            archive.writestr("word/vbaProject.bin", "macro")
        with self.assertRaises(ValidationError):
            validate_content(file, "macro.docx")

    def test_png_with_corrupt_checksum_is_rejected_as_client_validation(self):
        from PIL import Image
        valid = BytesIO()
        Image.new("RGB", (8, 8), "white").save(valid, format="PNG")
        damaged = bytearray(valid.getvalue())
        chunk = damaged.index(b"IDAT")
        size = int.from_bytes(damaged[chunk - 4:chunk], "big")
        damaged[chunk + 4 + size] ^= 1
        file = BytesIO(damaged)
        with self.assertRaisesMessage(ValidationError, "This file could not be read"):
            validate_content(file, "picture.png")
        self.assertEqual(file.tell(), 0)

    def test_spoofed_image_and_unsafe_filename_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_content(BytesIO(b"<script>not an image</script>"), "picture.png")
        for name in ["../secret.pdf", "folder\\secret.pdf", "file:secret.pdf", "evil.svg"]:
            with self.assertRaises(ValidationError):
                checked_name(name, 10)

    def test_presigned_policy_restricts_key_type_size_and_requires_private_bucket(self):
        storage = Mock()
        storage.bucket_name = "private-intake-test"
        storage._normalize_name.side_effect = lambda key: "intake/" + key
        client = storage.connection.meta.client
        client.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}
        upload = Mock(upload_key="staged/random.pdf", content_type="application/pdf", size_bytes=100)
        with patch("workqueue.uploads.private_storage", return_value=storage):
            direct_upload_policy(upload)
        call = client.generate_presigned_post.call_args.kwargs
        self.assertEqual(call["Key"], "intake/staged/random.pdf")
        self.assertIn(["content-length-range", 100, 100], call["Conditions"])
        self.assertEqual(call["ExpiresIn"], 900)
        client.get_public_access_block.return_value["PublicAccessBlockConfiguration"]["BlockPublicPolicy"] = False
        from django.core.exceptions import ImproperlyConfigured
        with patch("workqueue.uploads.private_storage", return_value=storage), self.assertRaises(ImproperlyConfigured):
            direct_upload_policy(upload)
