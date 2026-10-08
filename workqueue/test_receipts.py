import re
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Attachment, NotificationDelivery, Submission, WorkItem
from .notifications import deliver_pending
from .services import edit_work
from .test_client_intake import new_answers, valid_pdf


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, RECAPTCHA_ENTERPRISE_API_KEY="",
    RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY="", WORK_INTAKE_ENABLED=True,
    WORK_QUEUE_ENABLED=True, INTAKE_DIRECT_UPLOADS=False, INTAKE_OWNER_EMAIL="owner@example.invalid",
    INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubmissionReceiptTests(TestCase):
    def setUp(self):
        cache.clear()
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        storage = override_settings(STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "intake_private": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.files.name, "base_url": None}}})
        storage.enable()
        self.addCleanup(storage.disable)
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]

    def submit(self, **changes):
        response = self.client.post(reverse("workqueue:submit"), new_answers(intake_token=self.token, **changes))
        self.assertEqual(response.status_code, 302, response.content.decode()[:1000])
        return WorkItem.objects.latest("received_at")

    def receipt(self, item):
        return NotificationDelivery.objects.get(submission__work_item=item, recipient_kind="client")

    def upload(self, name):
        response = self.client.post(reverse("workqueue:start_upload"), {"intake_token": self.token, "file": valid_pdf(name)})
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def verify_receipt(self, client, body):
        token = re.search(r"#token=([A-Za-z0-9_-]+)", body)[1]
        response = client.post(reverse("workqueue:confirm_access"), {"token": token})
        self.assertEqual(response.status_code, 302)

    def test_new_receipt_includes_every_completed_answer_with_clear_labels(self):
        values = {"new_description": "First floor addition.\nKeep the existing stair.",
            "same_address": "on", "approximate_size": "600 square feet", "timeframe": "As soon as available",
            "file_link": "https://example.invalid/shared-plans", "requested_deadline": "2026-12-15",
            "plan_number": "PHD-DEMO-123", "preferred_contact": "Phone", "referral_detail": "Our builder",
            "notes": "Please call after 3.\nAsk for Alex.", "home_preferences": "Three bedrooms",
            "plan_changes": "Larger windows", "framing_scope": "Coordinate kitchen beam",
            "site_constraints": "HOA review", "project_contacts": "Sam Example, builder"}
        item = self.submit(**values)
        body = self.receipt(item).body
        self.assertIn("YOUR SUBMISSION RECORD", body)
        self.assertIn("Submitted:", body)
        self.assertIn("Is this a new submission or an update?\nNew Submission", body)
        self.assertIn("Terms accepted:\nYes", body)
        self.assertIn("Project address is the same as billing address:\nYes", body)
        self.assertIn("Terms & Conditions:\nhttps://www.provosthomedesign.com/terms/", body)
        for name, value in new_answers(**values).items():
            if name not in {"terms_accepted", "kind", "same_address"}:
                self.assertIn(value, body, name)
        self.assertIn("UPLOADED DOCUMENTS (0)", body)
        self.assertIn("No files uploaded.", body)
        self.assertIn("https://www.provosthomedesign.com/track-work/", body)
        self.assertNotIn("None", body)

    def test_only_submitted_files_are_listed_with_categories_sizes_and_private_links(self):
        first, second, unused = [self.upload(name) for name in ["Plans.pdf", "Bank survey.pdf", "Not submitted.pdf"]]
        item = self.submit(upload_ids=first + "," + second, categories=["Plans or survey", "Photos or sketches"])
        body = self.receipt(item).body
        files = list(Attachment.objects.filter(submission__work_item=item))
        self.assertEqual(len(files), 2)
        self.assertIn("UPLOADED DOCUMENTS (2)", body)
        for attachment in files:
            self.assertIn(attachment.original_name + " (", body)
            self.assertIn("bytes)", body)
            self.assertIn("Categories: Plans or survey, Photos or sketches", body)
            self.assertIn("https://www.provosthomedesign.com" + reverse("workqueue:download", args=[attachment.pk]), body)
            self.assertNotIn(attachment.storage_key, body)
        self.assertNotIn("Not submitted.pdf", body)
        self.assertNotIn(unused, body)
        self.assertNotIn("amazonaws.com", body)
        self.assertIn("Documents are linked, not attached", body)
        # Receipt links preserve the existing verified-owner download boundary.
        client = Client()
        url = reverse("workqueue:download", args=[files[0].pk])
        self.assertEqual(client.get(url).status_code, 404)
        self.verify_receipt(client, body)
        download = client.get(url)
        self.assertEqual(download.status_code, 200)
        self.assertTrue(b"".join(download.streaming_content).startswith(b"%PDF-"))
        self.assertEqual(Client().get(url).status_code, 404)

    def test_update_receipt_captures_selected_project_and_only_its_own_answers_files(self):
        original_file = self.upload("Original plans.pdf")
        original = self.submit(upload_ids=original_file, categories=["Plans or survey"], notes="Original private client notes")
        self.verify_receipt(self.client, self.receipt(original).body)
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        update_file = self.upload("Revised plan.pdf")
        update = self.submit(kind="update", project=str(original.project_id),
            update_description="Move the kitchen window two feet.", categories=["Revision request", "Answers or corrections"],
            upload_ids=update_file, notes="This update is for the bank.")
        body = self.receipt(update).body
        self.assertEqual(update.project_id, original.project_id)
        self.assertIn("Is this a new submission or an update?\nUpdate", body)
        self.assertIn("Choose one of your projects:\n" + str(original.project), body)
        self.assertIn("Move the kitchen window two feet.", body)
        self.assertIn("This update is for the bank.", body)
        self.assertIn("Revised plan.pdf", body)
        self.assertNotIn("Original plans.pdf", body)
        self.assertNotIn("Original private client notes", body)
        self.assertNotIn("Billing street address", body)
        self.assertNotIn("1 Billing Road", body)
        self.assertIn("What are you sending?\nRevision request, Answers or corrections", body)
        self.assertEqual(Submission.objects.get(work_item=update).answers["Choose one of your projects"], str(original.project))

    def test_unverified_update_receipt_keeps_submitted_name_or_address_without_claiming_a_match(self):
        item = self.submit(kind="update", project_context="Lot 4, Example Road, Demo Town MA",
            update_description="Update framing", categories=["Other"])
        body = self.receipt(item).body
        self.assertIsNone(item.project_id)
        self.assertIn("Project name or full address:\nLot 4, Example Road, Demo Town MA", body)
        self.assertNotIn("Connected project:", body)

    def test_receipt_is_frozen_before_staff_edits_and_delivers_without_loading_file_bodies(self):
        upload = self.upload("Large plan.pdf")
        item = self.submit(upload_ids=upload, categories=["Plans or survey"], notes="Submitted note")
        original_body = self.receipt(item).body
        edit_work(item_id=item.pk, version=item.version, actor=None,
                  data={"internal_notes": "PRIVATE STAFF NOTE", "queue_order": 99})
        item.project.name = "STAFF RENAMED PROJECT"
        item.project.save()
        Submission.objects.filter(work_item=item).update(answers={"Anything else we should know?": "Changed archive"})
        with patch("django.core.files.storage.FileSystemStorage.open", side_effect=AssertionError("Email must not read attachments")):
            self.assertEqual(deliver_pending(), 2)
        message = next(message for message in mail.outbox if message.to == [item.contact_email])
        self.assertEqual(message.body, original_body)
        self.assertEqual(message.attachments, [])
        self.assertNotIn("PRIVATE STAFF NOTE", message.body)
        self.assertNotIn("STAFF RENAMED PROJECT", message.body)
        self.assertNotIn("Changed archive", message.body)
        self.assertNotIn(self.token, message.body)
        self.assertNotIn("/work-queue/", message.body)
        self.assertIn("Position when submitted: 1", message.body)
        self.assertEqual(self.receipt(item).body, "")

    def test_repeated_submission_does_not_send_another_receipt_or_add_new_answers(self):
        item = self.submit(notes="Original submitted note")
        self.submit(notes="Retry changed note")
        self.assertEqual(WorkItem.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertIn("Original submitted note", self.receipt(item).body)
        self.assertNotIn("Retry changed note", self.receipt(item).body)
        self.assertEqual(deliver_pending(), 2)
        self.assertEqual(deliver_pending(), 0)

    def test_unknown_or_internal_archived_keys_never_leak_into_client_receipt(self):
        from .receipts import submitted_answers
        item = self.submit()
        submission = Submission.objects.get(work_item=item)
        submission.answers.update({"internal_notes": "PRIVATE", "queue_order": "999", "intake_token": "secret",
                                   "Some future staff field": "STAFF DATA"})
        from .notifications import site_url
        body = submitted_answers(submission, site_url)
        for value in ["PRIVATE", "999", "secret", "STAFF DATA"]:
            self.assertNotIn(value, body)
