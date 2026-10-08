import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .models import Attachment, InformationRequest, NotificationDelivery, QueueState, Status, WorkItem
from .information_requests import request_information
from .notifications import deliver_pending
from .services import EditConflict, create_work, edit_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
                   INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
class QueueUpgradeTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("upgrade-owner", "owner@example.invalid", "test-only")
        self.client.force_login(self.owner)
        self.item = create_work(data=work_data(internal_notes="PRIVATE INTERNAL NOTE"), actor=self.owner)[0]
        self.url = reverse("workqueue:queue")

    def payload(self, **changes):
        prefix = f"information-{self.item.pk}-"
        data = {"action": "preview_information", "item_id": str(self.item.pk), "filter_scope": "active",
                "filter_attention": "followup", prefix + "version": self.item.version,
                prefix + "token": str(uuid.uuid4()), prefix + "message": "Please send your survey and two site photos.",
                prefix + "followup_date": timezone.localdate().isoformat()}
        data.update(changes)
        return data

    def preview(self):
        data = self.payload()
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context["rows"] if row["item"].pk == self.item.pk)
        data.update(action="send_information", preview_signature=row["information_signature"])
        return data, response

    def counts(self, response):
        return {entry["key"]: entry["count"] for entry in response.context["attention_summary"]}

    def test_attention_date_boundaries_and_closed_work(self):
        today = timezone.localdate()
        WorkItem.objects.filter(pk=self.item.pk).update(committed_due_date=today-timedelta(days=1), followup_date=today)
        create_work(data=work_data(committed_due_date=today, followup_date=today+timedelta(days=1)), actor=self.owner)
        create_work(data=work_data(status=Status.COMPLETED, committed_due_date=today-timedelta(days=1), followup_date=today), actor=self.owner)
        counts = self.counts(self.client.get(self.url))
        self.assertEqual(counts["overdue"], 1)
        self.assertEqual(counts["followup"], 1)
        response = self.client.get(self.url, {"attention": "overdue", "scope": "all"})
        self.assertEqual([row["item"].pk for row in response.context["rows"]], [self.item.pk])
        self.assertContains(response, "Follow-up due")

    def test_attention_is_global_and_position_does_not_reset_on_filter(self):
        second = create_work(data=work_data(status=Status.NEEDS_INFORMATION), actor=self.owner)[0]
        response = self.client.get(self.url, {"q": second.reference, "attention": "information"})
        self.assertEqual(self.counts(response)["information"], 1)
        self.assertEqual(response.context["rows"][0]["item"].position, 2)
        response = self.client.get(self.url, {"q": "No match"})
        self.assertEqual(self.counts(response)["information"], 1)

    def test_unknown_attention_is_ignored_and_attention_survives_status_save(self):
        response = self.client.get(self.url, {"attention": "unexpected"})
        self.assertEqual(response.context["filters"]["attention"], "")
        response = self.client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "on_hold", "filter_attention": "followup", "filter_scope": "all"})
        self.assertIn("attention=followup", response.url)

    def test_attachment_access_on_row_without_opening_manage(self):
        submission = self.item.submissions.get()
        attachment = Attachment.objects.create(submission=submission, original_name="Kitchen plan.pdf",
            storage_key="intake/private/plan.pdf", size_bytes=1234)
        Attachment.objects.create(submission=submission, original_name="Old survey.pdf", legacy_drive_id="abcdefghij123")
        response = self.client.get(self.url)
        self.assertContains(response, "Files (2)")
        self.assertContains(response, reverse("workqueue:download", args=[attachment.pk]))
        self.assertEqual(len(response.context["rows"][0]["files"]), 2)
        other = create_work(data=work_data(), actor=self.owner)[0]
        response = self.client.get(self.url, {"q": other.reference})
        self.assertNotContains(response, "Kitchen plan.pdf")

    def test_preview_creates_no_email_or_status_change_and_contains_no_internal_notes(self):
        data, response = self.preview()
        self.assertContains(response, "Review your email")
        self.assertContains(response, "Send email &amp; mark Needs Information")
        row = next(row for row in response.context["rows"] if row["item"].pk == self.item.pk)
        preview = row["information_preview"]
        self.assertEqual(preview["recipient"], self.item.contact_email)
        self.assertIn(f"kind=update&reference={self.item.reference}", preview["body"])
        self.assertNotIn("PRIVATE INTERNAL NOTE", preview["body"])
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(InformationRequest.objects.count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.NEW)

    def test_send_records_message_preserves_position_and_delivers_through_existing_outbox(self):
        data, _ = self.preview()
        order = self.item.queue_order
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("attention=followup", response.url)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.NEEDS_INFORMATION)
        self.assertEqual(self.item.followup_date, timezone.localdate())
        self.assertEqual(self.item.queue_order, order)
        note = InformationRequest.objects.get()
        self.assertEqual(note.actor, self.owner)
        self.assertEqual(note.delivery.state, "pending")
        self.assertTrue(self.item.audit_events.filter(action="information_requested").exists())
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.item.contact_email])
        self.assertNotIn("PRIVATE INTERNAL NOTE", mail.outbox[0].body)
        note.delivery.refresh_from_db()
        self.assertEqual(note.delivery.state, "sent")
        self.assertEqual(note.delivery.body, "")
        note.refresh_from_db()
        self.assertIn("survey", note.message)

    def test_repeat_send_is_idempotent_even_after_version_changes(self):
        data, _ = self.preview()
        self.client.post(self.url, data)
        self.item.refresh_from_db()
        edit_work(item_id=self.item.pk, version=self.item.version, data={"status": Status.IN_PROGRESS}, actor=self.owner)
        response = self.client.post(self.url, data, follow=True)
        self.assertContains(response, "no second email was created")
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(InformationRequest.objects.count(), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.IN_PROGRESS)

    def test_send_requires_preview_and_unchanged_reviewed_fields(self):
        data = self.payload(action="send_information")
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        data, _ = self.preview()
        data[f"information-{self.item.pk}-message"] = "Changed after preview"
        response = self.client.post(self.url, data)
        self.assertContains(response, "changed since its preview", status_code=400)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_stale_send_does_not_send_and_keeps_draft_for_new_preview(self):
        data, _ = self.preview()
        edit_work(item_id=self.item.pk, version=1, data={"status": Status.ON_HOLD}, actor=self.owner)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Please send your survey", status_code=409)
        row = next(row for row in response.context["rows"] if row["item"].pk == self.item.pk)
        self.assertEqual(row["information_form"].data[f"information-{self.item.pk}-version"], "2")
        self.assertIsNone(row["information_preview"])
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_closed_request_cannot_be_marked_needs_information_by_email(self):
        edit_work(item_id=self.item.pk, version=1, data={"status": Status.COMPLETED}, actor=self.owner)
        self.item.refresh_from_db()
        response = self.client.post(self.url, self.payload())
        self.assertContains(response, "Reopen this request", status_code=400)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_invalid_message_and_date_create_no_email(self):
        for changes in [{f"information-{self.item.pk}-message": " "},
                        {f"information-{self.item.pk}-followup_date": "not a date"},
                        {f"information-{self.item.pk}-message": "x" * 5001}]:
            response = self.client.post(self.url, self.payload(**changes))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_view_only_staff_cannot_preview_or_send(self):
        staff = get_user_model().objects.create_user("upgrade-reader", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.client.force_login(staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Preview email")
        for action in ["preview_information", "send_information"]:
            self.assertEqual(self.client.post(self.url, self.payload(action=action)).status_code, 403)

    def test_sending_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, self.payload()).status_code, 403)

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_information_tool_disabled_when_client_intake_is_disabled(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 404)
        self.assertNotContains(self.client.get(self.url), "Preview email")

    def test_email_attention_includes_closed_receipts_and_information_requests_without_duplicates(self):
        data, _ = self.preview()
        self.client.post(self.url, data)
        note = InformationRequest.objects.get()
        NotificationDelivery.objects.filter(pk=note.delivery_id).update(state="unknown")
        NotificationDelivery.objects.create(submission=self.item.submissions.get(), recipient_kind="client",
            recipient=self.item.contact_email, state="failed")
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        response = self.client.get(self.url, {"scope": "all", "attention": "email"})
        self.assertEqual(self.counts(response)["email"], 1)
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertContains(response, "Email needs attention")
        self.assertContains(response, "Please send your survey")

    def test_information_email_retry_uses_same_record_and_checks_ambiguous_delivery(self):
        data, _ = self.preview()
        self.client.post(self.url, data)
        note = InformationRequest.objects.get()
        NotificationDelivery.objects.filter(pk=note.delivery_id).update(state="unknown")
        retry = {"action": "retry_notice", "item_id": self.item.pk, "notice_id": note.delivery_id}
        self.assertEqual(self.client.post(self.url, retry).status_code, 400)
        retry["not_sent_confirmed"] = "yes"
        self.assertEqual(self.client.post(self.url, retry).status_code, 302)
        note.delivery.refresh_from_db()
        self.assertEqual(note.delivery.state, "pending")
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        other = create_work(data=work_data(), actor=self.owner)[0]
        retry["item_id"] = other.pk
        self.assertEqual(self.client.post(self.url, retry).status_code, 404)

    def test_update_link_prefills_id_but_never_discloses_project_or_contact(self):
        self.client.logout()
        response = self.client.get(reverse("workqueue:submit"), {"kind": "update", "reference": self.item.reference})
        self.assertContains(response, f'value="{self.item.reference}"')
        self.assertNotContains(response, self.item.contact_email)
        self.assertNotContains(response, self.item.project_name)
        self.assertNotContains(response, "PRIVATE INTERNAL NOTE")
        invalid = self.client.get(reverse("workqueue:submit"), {"kind": "update", "reference": "<script>"})
        self.assertEqual(invalid.context["form"].initial.get("project_reference", ""), "")


@skipUnlessDBFeature("has_select_for_update")
@override_settings(INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
class InformationRequestConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_user("information-concurrency", is_staff=True)
        self.item = create_work(data=work_data(), actor=self.owner)[0]

    def send_parallel(self, tokens):
        def send(token):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.owner.pk)
                try:
                    _, created = request_information(item_id=self.item.pk, version=1,
                        message="Please send the survey.", followup_date=None, token=token, actor=actor)
                except EditConflict:
                    return "conflict"
                return "created" if created else "already queued"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(send, tokens))

    def test_simultaneous_clicks_of_same_send_create_one_email(self):
        token = uuid.uuid4()
        self.assertCountEqual(self.send_parallel([token, token]), ["created", "already queued"])
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(InformationRequest.objects.count(), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.version, 2)

    def test_two_different_stale_previews_cannot_both_send(self):
        self.assertCountEqual(self.send_parallel([uuid.uuid4(), uuid.uuid4()]), ["created", "conflict"])
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(InformationRequest.objects.count(), 1)
