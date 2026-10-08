import uuid
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail, signing
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .models import Attachment, NotificationDelivery, QueueState, Status, StatusMilestone, WorkItem
from .notifications import deliver_pending
from .services import EditConflict, create_work, edit_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
    INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
class QueueWorkflowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("workflow-owner", "owner@example.invalid", "test-only")
        self.client.force_login(self.owner)
        self.url = reverse("workqueue:queue")
        self.arrivals = reverse("workqueue:arrivals")
        self.item = create_work(data=work_data(internal_notes="PRIVATE STAFF NOTES"), actor=self.owner)[0]

    def change(self, status, choice=None, version=None):
        self.item.refresh_from_db()
        data = {"action": "status", "item_id": self.item.pk, "version": version or self.item.version,
                "status": status, "filter_attention": "followup", "filter_scope": "all"}
        if choice is not None:
            data["client_email"] = choice
        return self.client.post(self.url, data)

    def snapshot(self):
        return self.client.get(self.url).context["arrival_snapshot"]

    def test_skip_is_default_and_every_milestone_decision_is_recorded(self):
        response = self.change(Status.IN_PROGRESS)
        self.assertEqual(response.status_code, 302)
        self.assertIn("attention=followup", response.url)
        milestone = StatusMilestone.objects.get()
        self.assertIsNone(milestone.delivery_id)
        self.assertEqual(milestone.before_status, Status.NEW)
        self.assertEqual(milestone.status, Status.IN_PROGRESS)
        self.assertEqual(milestone.actor, self.owner)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertTrue(self.item.audit_events.filter(action="milestone_email_decided").exists())
        self.assertContains(self.client.get(self.url), "Email skipped")

    def test_start_and_completion_send_allowlisted_email_through_existing_outbox(self):
        order = self.item.queue_order
        for status, subject in [(Status.IN_PROGRESS, "Work has started"), (Status.COMPLETED, "Work is complete")]:
            response = self.change(status, "send")
            self.assertEqual(response.status_code, 302)
            milestone = self.item.milestones.latest("pk")
            self.assertEqual(milestone.delivery.recipient, self.item.contact_email)
            self.assertIn(subject, milestone.delivery.subject)
            self.assertIn(self.item.reference, milestone.delivery.body)
            self.assertIn("/track-work/?reference=" + self.item.reference, milestone.delivery.body)
            self.assertNotIn("PRIVATE STAFF NOTES", milestone.delivery.body)
            self.assertNotIn("508-555", milestone.delivery.body)
            self.assertEqual(deliver_pending(), 1)
            milestone.delivery.refresh_from_db()
            self.assertEqual(milestone.delivery.state, "sent")
            self.assertEqual(milestone.delivery.body, "")
        self.assertEqual(len(mail.outbox), 2)
        self.item.refresh_from_db()
        self.assertEqual(self.item.queue_order, order)
        self.assertEqual(self.item.status, Status.COMPLETED)

    def test_unchanged_status_nonmilestones_and_internal_edits_never_send(self):
        for status in [Status.NEW, Status.ON_HOLD, Status.READY, Status.SCHEDULED, Status.DECLINED]:
            self.assertEqual(self.change(status, "send").status_code, 302)
        self.item.refresh_from_db()
        edit_work(item_id=self.item.pk, version=self.item.version, data={"internal_notes": "Changed privately"},
                  notify_client=True, actor=self.owner)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(StatusMilestone.objects.count(), 0)

    def test_repeat_post_stale_version_and_repeated_status_do_not_duplicate_email(self):
        self.assertEqual(self.change(Status.IN_PROGRESS, "send").status_code, 302)
        self.assertEqual(self.change(Status.IN_PROGRESS, "send", version=1).status_code, 409)
        self.assertEqual(self.change(Status.IN_PROGRESS, "send").status_code, 302)
        self.assertEqual(StatusMilestone.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_reopened_work_can_send_a_new_genuine_milestone(self):
        for status in [Status.IN_PROGRESS, Status.COMPLETED, Status.ON_HOLD, Status.IN_PROGRESS]:
            self.assertEqual(self.change(status, "send").status_code, 302)
        self.assertEqual(StatusMilestone.objects.count(), 3)
        self.assertEqual(NotificationDelivery.objects.count(), 3)

    def test_full_edit_keeps_internal_notes_out_of_email(self):
        self.item.refresh_from_db()
        data = {f"{self.item.pk}-{name}": getattr(self.item, name) or "" for name in
                ["status", "priority", "queue_order", "estimated_days", "requested_deadline", "committed_due_date",
                 "scheduled_start", "estimated_completion", "followup_date", "internal_notes"]}
        data.update({"action": "edit", "item_id": str(self.item.pk), f"{self.item.pk}-version": 1,
                     f"{self.item.pk}-project": str(self.item.project_id), f"{self.item.pk}-status": "in_progress",
                     f"{self.item.pk}-client_email": "send", f"{self.item.pk}-internal_notes": "New private note"})
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.internal_notes, "New private note")
        self.assertNotIn("private note", NotificationDelivery.objects.get().body)

    def test_invalid_choice_or_failed_email_preparation_rolls_back_save(self):
        self.assertEqual(self.change(Status.COMPLETED, "unexpected").status_code, 400)
        with patch("workqueue.milestones.site_url", side_effect=ValidationError("Email unavailable")):
            self.assertEqual(self.change(Status.COMPLETED, "send").status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.NEW)
        self.assertEqual(self.item.version, 1)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(StatusMilestone.objects.count(), 0)

    def test_validation_for_field_outside_status_form_is_visible_and_does_not_crash(self):
        WorkItem.objects.filter(pk=self.item.pk).update(
            scheduled_start=date(2026, 10, 9), estimated_completion=date(2026, 10, 8))
        response = self.change(Status.COMPLETED, 'skip')
        self.assertContains(response, 'Estimated completion cannot precede the scheduled start.', status_code=400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.NEW)
        self.assertEqual(self.item.version, 1)
        self.assertEqual(StatusMilestone.objects.count(), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_disabled_email_worker_requires_skip_to_save_milestone(self):
        self.assertEqual(self.change(Status.IN_PROGRESS, "send").status_code, 400)
        self.assertEqual(self.change(Status.IN_PROGRESS, "skip").status_code, 302)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_email_failure_visible_on_closed_work_and_retry_checks_ownership_and_uncertainty(self):
        self.change(Status.COMPLETED, "send")
        notice = NotificationDelivery.objects.get()
        with patch("workqueue.notifications.EmailMessage.send", side_effect=OSError("provider interrupted")):
            deliver_pending()
        response = self.client.get(self.url, {"scope": "all", "attention": "email"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertContains(response, "Needs reconciliation")
        retry = {"action": "retry_notice", "item_id": self.item.pk, "notice_id": notice.pk}
        self.assertEqual(self.client.post(self.url, retry).status_code, 400)
        retry["not_sent_confirmed"] = "yes"
        self.assertEqual(self.client.post(self.url, retry).status_code, 302)
        notice.refresh_from_db()
        self.assertEqual(notice.state, "pending")
        other = create_work(data=work_data(), actor=self.owner)[0]
        retry["item_id"] = other.pk
        self.assertEqual(self.client.post(self.url, retry).status_code, 404)

    def test_staff_permissions_and_csrf_are_required_for_send(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed", "client_email": "send"}).status_code, 403)
        reader = get_user_model().objects.create_user("workflow-reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.client.force_login(reader)
        self.assertEqual(self.change(Status.COMPLETED, "send").status_code, 403)
        self.assertNotContains(self.client.get(self.url), "Send milestone email")
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_files_count_includes_each_request_attachment_and_opens_in_separate_tab(self):
        Attachment.objects.create(submission=self.item.submissions.get(), original_name="Site survey.pdf",
            legacy_drive_id="exampleDriveIdentifier")
        other = create_work(data=work_data(project=self.item.project), actor=self.owner)[0]
        response = self.client.get(self.url)
        self.assertContains(response, f'aria-label="Files (1) for {self.item.reference}"')
        self.assertContains(response, 'target="_blank" rel="noopener">Site survey.pdf')
        rows = {row["item"].pk: row for row in response.context["rows"]}
        self.assertEqual(len(rows[self.item.pk]["files"]), 1)
        self.assertEqual(len(rows[other.pk]["files"]), 0)
        self.assertContains(response, "0 files")

    def test_arrival_count_is_global_and_only_advances_for_committed_new_work(self):
        snapshot = self.snapshot()
        self.assertEqual(self.client.get(self.arrivals, {"snapshot": snapshot}).json(), {"count": 0})
        self.change(Status.IN_PROGRESS, "skip")
        self.assertEqual(self.client.get(self.arrivals, {"snapshot": snapshot}).json(), {"count": 0})
        data = work_data(kind="update", project=self.item.project)
        create_work(data=data, actor=self.owner, idempotency_key="workflow:update")
        create_work(data=data, actor=self.owner, idempotency_key="workflow:update")
        create_work(data=work_data(status=Status.COMPLETED), actor=self.owner)
        response = self.client.get(self.arrivals, {"snapshot": snapshot, "q": "not present", "scope": "closed"})
        self.assertEqual(response.json(), {"count": 2})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get(self.arrivals, {"snapshot": self.snapshot()}).json(), {"count": 0})

    def test_arrival_poll_has_no_writes_and_requires_signed_baseline_staff_and_feature(self):
        snapshot = self.snapshot()
        for bad in ["", "123", snapshot + "x", signing.dumps("bad", salt="workqueue.arrivals.v1")]:
            self.assertEqual(self.client.get(self.arrivals, {"snapshot": bad}).status_code, 400)
        self.assertEqual(self.client.post(self.arrivals, {"snapshot": snapshot}).status_code, 405)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=False):
            self.assertEqual(self.client.get(self.arrivals, {"snapshot": snapshot}).status_code, 404)
        nonstaff = get_user_model().objects.create_user("workflow-public")
        self.client.force_login(nonstaff)
        self.assertEqual(self.client.get(self.arrivals, {"snapshot": snapshot}).status_code, 302)
        nonstaff.is_staff = True
        nonstaff.save()
        self.assertEqual(self.client.get(self.arrivals, {"snapshot": snapshot}).status_code, 403)
        self.client.logout()
        response = self.client.get(self.arrivals, {"snapshot": snapshot})
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(response, '"count"', status_code=302)


@skipUnlessDBFeature("has_select_for_update")
@override_settings(WORK_INTAKE_ENABLED=True, INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
class MilestoneConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_user("milestone-concurrent", is_staff=True)
        self.item = create_work(data=work_data(), actor=self.owner)[0]

    def test_simultaneous_saves_create_one_transition_and_one_email(self):
        def save(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.owner.pk)
                try:
                    edit_work(item_id=self.item.pk, version=1, data={"status": Status.COMPLETED},
                              notify_client=True, actor=actor)
                    return "saved"
                except EditConflict:
                    return "conflict"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(save, range(2))), ["saved", "conflict"])
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(StatusMilestone.objects.count(), 1)
