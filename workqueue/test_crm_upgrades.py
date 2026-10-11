import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .crm import (archive_contact, change_followup, edit_client, log_activity, merge_clients,
                  save_contact, save_followup, undo_merge)
from .crm_overview import overview, possible_match_ids, timeline
from .crm_views import client_counts
from .models import (Attachment, Client, ClientEvent, ClientFollowUp, NotificationDelivery,
                     ProjectAccess, Status, WorkItem)
from .services import EditConflict, create_work, delete_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True)
class CRMUpgradeTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("upgrades-owner", "owner@example.invalid", "local-only")
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.contact = self.item.client_contact
        self.account = self.contact.client
        self.client.force_login(self.owner)
        self.url = reverse("workqueue:client_detail", args=[self.account.pk])
        self.directory = reverse("workqueue:clients")

    def task(self, **values):
        self.account.refresh_from_db()
        data = {"title": "Send estimate", "due_date": timezone.localdate(), "notes": "PRIVATE TASK"}
        data.update(values)
        return save_followup(client_id=self.account.pk, version=self.account.version, task_id=uuid.uuid4(),
                             task_version=None, data=data, actor=self.owner)

    def task_state(self, task, action):
        self.account.refresh_from_db()
        return change_followup(client_id=self.account.pk, version=self.account.version, task_id=task.pk,
                               task_version=task.version, action=action, actor=self.owner)

    def test_automatic_stage_counts_and_manual_override(self):
        account = client_counts(Client.objects.all()).get(pk=self.account.pk)
        self.assertEqual(account.stage_label, "Active client")
        self.item.status = Status.COMPLETED
        self.item.save()
        self.assertContains(self.client.get(self.directory, {"stage": "past"}), self.account.name)
        self.account.refresh_from_db()
        edit_client(client_id=self.account.pk, version=self.account.version, data={"stage": "estimate"}, actor=self.owner)
        self.assertContains(self.client.get(self.directory, {"stage": "estimate"}), self.account.name)
        self.assertNotContains(self.client.get(self.directory, {"stage": "past"}), self.url)
        self.assertContains(self.client.get(self.url), "Estimate sent")

    def test_task_lifecycle_reschedule_and_due_list_without_email(self):
        task = self.task()
        response = self.client.get(self.directory, {"attention": "due"})
        self.assertContains(response, "Send estimate")
        self.assertContains(self.client.get(self.url), "PRIVATE TASK")
        task = self.task_state(task, "complete")
        self.assertFalse(task.is_open)
        self.assertNotContains(self.client.get(self.directory, {"attention": "due"}), self.url)
        task = self.task_state(task, "reopen")
        task = self.task_state(task, "cancel")
        self.assertIsNotNone(task.canceled_at)
        task = self.task_state(task, "reopen")
        self.account.refresh_from_db()
        task = save_followup(client_id=self.account.pk, version=self.account.version, task_id=task.pk,
            task_version=task.version, actor=self.owner,
            data={"title": "Call tomorrow", "due_date": timezone.localdate()+timedelta(days=1), "notes": "Updated"})
        self.assertFalse(task.is_due)
        self.assertEqual(ClientFollowUp.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(ProjectAccess.objects.exists())

    def test_task_stale_retry_and_foreign_task_rejected(self):
        version = self.account.version
        task = self.task()
        with self.assertRaises(EditConflict):
            save_followup(client_id=self.account.pk, version=version, task_id=uuid.uuid4(), task_version=None,
                data={"title": "Duplicate", "due_date": timezone.localdate(), "notes": ""}, actor=self.owner)
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.owner)[0].client_contact.client
        with self.assertRaises(ValidationError):
            change_followup(client_id=other.pk, version=other.version, task_id=task.pk, task_version=task.version,
                            action="complete", actor=self.owner)
        self.account.refresh_from_db()
        with self.assertRaises(EditConflict):
            change_followup(client_id=self.account.pk, version=self.account.version, task_id=task.pk,
                            task_version=99, action="complete", actor=self.owner)
        self.assertEqual(ClientFollowUp.objects.count(), 1)

    def test_task_post_edit_and_state_validation(self):
        task_id = uuid.uuid4()
        post = {"action": "save_followup", "followup-version": self.account.version,
                "followup-task_id": task_id, "followup-title": "Call client",
                "followup-due_date": timezone.localdate().isoformat(), "followup-notes": "Staff only"}
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.assertEqual(self.client.post(self.url, post).status_code, 409)
        task = ClientFollowUp.objects.get(pk=task_id)
        self.account.refresh_from_db()
        prefix = f"task-{task.pk}"
        edit = {"action": "save_followup", "task_key": task.pk, f"{prefix}-version": self.account.version,
            f"{prefix}-task_id": task.pk, f"{prefix}-task_version": task.version,
            f"{prefix}-title": "Send quote", f"{prefix}-due_date": timezone.localdate().isoformat()}
        self.assertEqual(self.client.post(self.url, edit).status_code, 302)
        self.account.refresh_from_db(); task.refresh_from_db()
        state = {"action": "followup_state", "version": self.account.version,
                 "task_id": task.pk, "task_version": task.version, "task_action": "complete"}
        self.assertEqual(self.client.post(self.url, state).status_code, 302)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(self.client.post(self.url, {"action": "followup_state"}).status_code, 400)

    def test_queue_reminders_are_visible_without_new_tasks_and_deleted_closed_are_excluded(self):
        self.item.followup_date = timezone.localdate()
        self.item.save()
        self.assertContains(self.client.get(self.directory, {"attention": "due"}), "Job follow-up: " + self.item.reference)
        self.assertContains(self.client.get(self.url), "Existing queue reminder")
        self.assertFalse(ClientFollowUp.objects.exists())
        self.item.status = Status.COMPLETED
        self.item.save()
        self.assertNotContains(self.client.get(self.directory, {"attention": "due"}), self.url)
        self.item.status = Status.NEW
        self.item.save()
        delete_work(item_id=self.item.pk, version=self.item.version, actor=self.owner)
        self.assertNotContains(self.client.get(self.directory, {"attention": "due"}), self.url)

    def test_primary_role_edit_archive_and_snapshot_privacy(self):
        before = self.item.submissions.get().answers.copy()
        save_contact(client_id=self.account.pk, version=self.account.version, contact_id=self.contact.pk,
            data={"full_name": "Morgan", "email": self.contact.email, "phone": self.contact.phone,
                  "role": "contractor", "is_primary": True}, actor=self.owner)
        self.account.refresh_from_db()
        self.assertEqual(self.account.primary_contact_id, self.contact.pk)
        self.assertContains(self.client.get(self.url), "Contractor / builder")
        self.assertContains(self.client.get(self.directory), "Primary")
        self.assertEqual(self.item.submissions.get().answers, before)
        archive_contact(client_id=self.account.pk, version=self.account.version, contact_id=self.contact.pk, actor=self.owner)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.primary_contact_id)
        self.assertEqual(len(mail.outbox), 0)

    def test_older_open_contact_form_preserves_role_and_primary(self):
        self.contact.role = "billing"; self.contact.save()
        self.account.primary_contact = self.contact; self.account.save()
        prefix = f"contact-{self.contact.pk}"
        post = {"action": "contact", "contact_key": self.contact.pk, f"{prefix}-contact_id": self.contact.pk,
                f"{prefix}-version": self.account.version, f"{prefix}-full_name": "New name", f"{prefix}-phone": "123"}
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.contact.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual(self.contact.role, "billing")
        self.assertEqual(self.account.primary_contact_id, self.contact.pk)

    def test_merge_moves_tasks_and_primary_and_undo_restores(self):
        task = self.task()
        self.account.primary_contact = self.contact; self.account.save()
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.owner)[0].client_contact.client
        target = merge_clients(source_id=self.account.pk, source_version=self.account.version,
            target_id=other.pk, target_version=other.version, actor=self.owner)
        task.refresh_from_db()
        self.assertEqual(task.client_id, target.pk)
        self.assertEqual(target.primary_contact_id, self.contact.pk)
        event = target.events.get(action="Client records merged")
        source = undo_merge(client_id=target.pk, version=target.version, event_id=event.pk, actor=self.owner)
        task.refresh_from_db(); target.refresh_from_db()
        self.assertEqual(task.client_id, source.pk)
        self.assertEqual(source.primary_contact_id, self.contact.pk)
        self.assertIsNone(target.primary_contact_id)

    def test_missing_fields_and_possible_matches_filters(self):
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.owner)[0].client_contact.client
        self.assertIn(self.account.pk, possible_match_ids())
        self.assertIn(other.pk, possible_match_ids())
        self.assertContains(self.client.get(self.directory, {"attention": "matches"}), "Review possible matches")
        self.contact.email = None; self.contact.phone = ""; self.contact.save()
        self.assertContains(self.client.get(self.directory, {"attention": "missing_email"}), self.url)
        self.assertContains(self.client.get(self.directory, {"attention": "missing_phone"}), self.url)

    def test_activity_is_dated_escaped_private_and_stale_safe(self):
        text = '<script>alert("PRIVATE CALL")</script>'
        post = {"action": "activity", "activity-version": self.account.version,
                "activity-kind": "call", "activity-note": text}
        self.assertEqual(self.client.post(self.url, post).status_code, 302)
        self.assertEqual(self.client.post(self.url, post).status_code, 409)
        page = self.client.get(self.url)
        self.assertContains(page, "Phone call logged")
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, text)
        self.assertNotIn(text, str(self.item.submissions.get().answers))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ClientEvent.objects.filter(action="Phone call logged").count(), 1)

    def test_timeline_has_files_sent_email_and_paginated_notes(self):
        submission = self.item.submissions.get()
        Attachment.objects.create(submission=submission, original_name="Submitted plan.pdf", storage_key="private/test.pdf")
        NotificationDelivery.objects.create(submission=submission, recipient_kind="client", recipient=self.contact.email,
            subject="Receipt subject", state="sent", sent_at=timezone.now())
        for index in range(30):
            ClientEvent.objects.create(client=self.account, action="Note logged", changes={"note": f"Timeline note {index}"})
        first = timeline(self.account, 1)
        self.assertEqual(len(first), 25)
        second = timeline(self.account, 2)
        self.assertTrue(any(entry["body"] == "Receipt subject" for entry in second))
        self.assertTrue(any(("Submitted plan.pdf", reverse("workqueue:download", args=[submission.attachments.get().pk])) in entry["files"] for entry in second))
        self.assertContains(self.client.get(self.url), "Older activity")

    def test_deleted_job_is_absent_from_timeline(self):
        submission = self.item.submissions.get()
        NotificationDelivery.objects.create(submission=submission, recipient_kind="client", recipient=self.contact.email,
            subject="Should disappear", state="sent", sent_at=timezone.now())
        delete_work(item_id=self.item.pk, version=self.item.version, actor=self.owner)
        entries = timeline(self.account, 1)
        self.assertFalse(any(entry["body"] == "Should disappear" for entry in entries))
        self.assertFalse(any(self.item.reference in entry["title"] for entry in entries))

    def test_merged_client_notes_join_timeline_and_undo_separates_them(self):
        log_activity(client_id=self.account.pk, version=self.account.version, kind="meeting",
                     note="Original client meeting", actor=self.owner)
        self.account.refresh_from_db()
        other = create_work(data=work_data(contact_email="other@example.com"), actor=self.owner)[0].client_contact.client
        target = merge_clients(source_id=self.account.pk, source_version=self.account.version,
            target_id=other.pk, target_version=other.version, actor=self.owner)
        self.assertTrue(any(row["body"] == "Original client meeting" for row in timeline(target, 1)))
        event = target.events.get(action="Client records merged")
        undo_merge(client_id=target.pk, version=target.version, event_id=event.pk, actor=self.owner)
        self.assertFalse(any(row["body"] == "Original client meeting" for row in timeline(target, 1)))

    def test_new_staff_writes_require_csrf_and_invalid_dates_preserve_input(self):
        from django.test import Client as BrowserClient
        checked = BrowserClient(enforce_csrf_checks=True)
        checked.force_login(self.owner)
        self.assertEqual(checked.post(self.url, {"action": "activity"}).status_code, 403)
        response = self.client.post(self.url, {"action": "save_followup", "followup-version": self.account.version,
            "followup-task_id": uuid.uuid4(), "followup-title": "Keep this action", "followup-due_date": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Keep this action", status_code=400)
        self.assertFalse(ClientFollowUp.objects.exists())

    def test_read_only_staff_anonymous_and_preview_cannot_write(self):
        reader = get_user_model().objects.create_user("upgrades-reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.client.force_login(reader)
        self.assertContains(self.client.get(self.url), "Client activity")
        self.assertNotContains(self.client.get(self.url), "Log private activity")
        self.assertEqual(self.client.post(self.url, {"action": "activity"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            log_activity(client_id=self.account.pk, version=self.account.version, kind="note", note="Forbidden", actor=reader)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(self.directory).status_code, 302)
