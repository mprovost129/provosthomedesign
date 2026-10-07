import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .models import AuditEvent, QueueState, Status, Submission, WorkItem, WorkProject
from .services import EditConflict, create_work, edit_work


def work_data(**changes):
    data = {"contact_full_name": "Morgan Lee", "company": "Homeowner", "contact_email": "morgan@example.com",
            "contact_phone": "508-555-0100", "description": "Addition design and framing coordination.",
            "project_name": "Park Street addition", "project_street": "7 Park St Unit 1",
            "project_city": "Rehoboth", "project_state": "MA", "project_zip": "02769"}
    data.update(changes)
    return data


class QueueServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("queue-owner", is_staff=True)

    def create(self, **changes):
        return create_work(data=work_data(**changes), actor=self.user)[0]

    def test_new_request_creates_project_and_preserves_zip_and_name(self):
        item = self.create()
        self.assertEqual(item.reference, "PHD-00001")
        self.assertEqual(item.project.canonical_reference, item.reference)
        self.assertEqual(item.project.zip_code, "02769")
        self.assertEqual(item.contact_full_name, "Morgan Lee")
        self.assertEqual(item.submission_position, 1)

    def test_update_has_own_position_and_same_project_without_reopening_original(self):
        original = self.create()
        edit_work(item_id=original.pk, version=1, data={"status": Status.COMPLETED}, actor=self.user)
        update = self.create(kind=WorkItem.Kind.UPDATE, project=original.project, previous_request=original)
        original.refresh_from_db()
        self.assertEqual(original.status, Status.COMPLETED)
        self.assertNotEqual(update.reference, original.reference)
        self.assertEqual(update.project_id, original.project_id)
        self.assertEqual(update.submission_position, 1)
        self.assertEqual(WorkItem.objects.with_position().get(pk=update.pk).position, 1)

    def test_idempotent_staff_submission_does_not_allocate_twice(self):
        first, created = create_work(data=work_data(), actor=self.user, idempotency_key="call:123")
        second, repeated = create_work(data=work_data(), actor=self.user, idempotency_key="call:123")
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(QueueState.objects.get(pk=1).last_reference_number, 1)

    def test_closed_statuses_leave_queue_hold_information_and_progress_remain(self):
        items = [self.create() for _ in range(7)]
        statuses = [Status.COMPLETED, Status.CANCELED, Status.DECLINED, Status.DUPLICATE,
                    Status.ON_HOLD, Status.NEEDS_INFORMATION, Status.IN_PROGRESS]
        for item, status in zip(items, statuses):
            edit_work(item_id=item.pk, version=1, data={"status": status}, actor=self.user)
        active = list(WorkItem.objects.active().with_position())
        self.assertEqual([item.status for item in active], statuses[4:])
        self.assertEqual([item.position for item in active], [1, 2, 3])
        self.assertIsNone(WorkItem.objects.with_position().get(pk=items[0].pk).position)

    def test_position_is_global_after_search_filter_and_priority_is_not_order(self):
        self.create(priority="urgent")
        second = self.create(contact_full_name="Another Client")
        found = WorkItem.objects.with_position().filter(contact_full_name="Another Client").get()
        self.assertEqual(found.position, 2)
        edit_work(item_id=second.pk, version=1, data={"priority": "urgent"}, actor=self.user)
        self.assertEqual(WorkItem.objects.with_position().get(pk=second.pk).position, 2)

    def test_queue_reorder_changes_current_positions_not_receipt_snapshots(self):
        first, second = self.create(), self.create()
        edit_work(item_id=first.pk, version=1, data={"queue_order": 10}, actor=self.user)
        self.assertEqual(WorkItem.objects.with_position().get(pk=second.pk).position, 1)
        self.assertEqual(second.submission_position, 2)
        third = self.create()
        self.assertEqual(third.queue_order, 11)
        self.assertEqual(WorkItem.objects.with_position().get(pk=third.pk).position, 3)

    def test_equal_order_and_timestamp_use_stable_uuid_tiebreaker(self):
        first, second = self.create(), self.create()
        WorkItem.objects.filter(pk=second.pk).update(queue_order=first.queue_order, received_at=first.received_at)
        active = list(WorkItem.objects.active().with_position())
        self.assertEqual([item.pk for item in active], sorted([first.pk, second.pk]))
        self.assertEqual([item.position for item in active], [1, 2])

    def test_stale_edit_preserves_newer_notes(self):
        item = self.create()
        edit_work(item_id=item.pk, version=1, data={"internal_notes": "Newer notes"}, actor=self.user)
        with self.assertRaises(EditConflict):
            edit_work(item_id=item.pk, version=1, data={"internal_notes": "Older notes"}, actor=self.user)
        item.refresh_from_db()
        self.assertEqual(item.internal_notes, "Newer notes")
        self.assertEqual(item.version, 2)

    def test_linking_unmatched_update_preserves_position_reference_and_snapshot(self):
        original = self.create()
        update = self.create(kind="update", project_context="Park Street addition", project_name="")
        self.assertTrue(update.needs_project_link)
        edit_work(item_id=update.pk, version=1, data={"project": original.project}, actor=self.user)
        update.refresh_from_db()
        self.assertEqual(update.project_id, original.project_id)
        self.assertEqual(update.reference, "PHD-00002")
        self.assertEqual(update.queue_order, 2)
        self.assertEqual(update.project_context, "Park Street addition")
        self.assertTrue(update.audit_events.filter(changes__has_key="project").exists())

    def test_invalid_edit_rolls_back_link_changes(self):
        original = self.create()
        update = self.create(kind="update", project=original.project, previous_request=original)
        other = WorkProject.objects.create(name="Other project")
        with self.assertRaises(ValidationError):
            edit_work(item_id=original.pk, version=1, data={"project": other, "status": "invalid"}, actor=self.user)
        original.refresh_from_db()
        update.refresh_from_db()
        self.assertEqual(original.project_id, update.project_id)
        self.assertEqual(update.previous_request_id, original.pk)
        self.assertEqual(update.version, 1)

    def test_relink_clears_incompatible_previous_link_with_audit(self):
        original = self.create()
        old_project = original.project
        update = self.create(kind="update", project=original.project, previous_request=original)
        other = WorkProject.objects.create(name="Other project")
        edit_work(item_id=original.pk, version=1, data={"project": other}, actor=self.user)
        update.refresh_from_db()
        self.assertIsNone(update.previous_request_id)
        self.assertEqual(update.version, 2)
        self.assertTrue(update.audit_events.filter(action="prior_link_cleared").exists())
        old_project.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(old_project.canonical_reference, update.reference)
        self.assertEqual(other.canonical_reference, original.reference)

    def test_invalid_submission_rolls_back_allocator(self):
        with self.assertRaises(ValidationError):
            self.create(contact_email="bad-email")
        self.assertEqual(WorkItem.objects.count(), 0)
        self.assertEqual(QueueState.objects.get(pk=1).last_reference_number, 0)

    def test_import_high_water_is_respected(self):
        QueueState.objects.filter(pk=1).update(last_reference_number=83, last_queue_order=120)
        item = self.create()
        self.assertEqual(item.reference, "PHD-00084")
        self.assertEqual(item.queue_order, 121)

    def test_completion_before_start_is_rejected(self):
        from datetime import date
        item = self.create()
        with self.assertRaises(ValidationError):
            edit_work(item_id=item.pk, version=1, actor=self.user,
                data={"scheduled_start": date(2026, 11, 2), "estimated_completion": date(2026, 11, 1)})
        item.refresh_from_db()
        self.assertIsNone(item.scheduled_start)


@override_settings(WORK_QUEUE_ENABLED=True)
class QueuePageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "test-password")
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.url = reverse("workqueue:queue")

    def login(self):
        self.client.force_login(self.owner)

    def test_anonymous_and_client_cannot_view_queue(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        customer = get_user_model().objects.create_user("client")
        self.client.force_login(customer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(response, self.item.contact_email, status_code=302)

    def test_staff_requires_explicit_queue_permission(self):
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Save status")
        self.assertNotContains(response, "Add work from a phone call or email")
        self.assertEqual(self.client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed"}).status_code, 403)

    def test_queue_renders_without_public_navigation_or_analytics_and_is_not_cached(self):
        self.login()
        response = self.client.get(self.url)
        self.assertContains(response, self.item.reference)
        self.assertContains(response, "Add work from a phone call or email")
        self.assertNotContains(response, "googletagmanager")
        self.assertIn("no-store", response["Cache-Control"])

    def test_complete_disappears_from_active_view_and_remains_in_all_work(self):
        self.login()
        response = self.client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 0)
        all_work = self.client.get(self.url, {"scope": "all"})
        self.assertEqual(len(all_work.context["rows"]), 1)
        self.assertContains(all_work, "Closed")

    def test_stale_status_returns_conflict_without_overwrite(self):
        self.login()
        edit_work(item_id=self.item.pk, version=1, data={"status": "on_hold"}, actor=self.owner)
        response = self.client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed"})
        self.assertEqual(response.status_code, 409)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "on_hold")
        self.assertContains(response, "changed in another tab", status_code=409)

    def edit_payload(self, **changes):
        data = {"status": self.item.status, "priority": self.item.priority, "queue_order": self.item.queue_order,
                "project": str(self.item.project_id), "estimated_days": "", "requested_deadline": "",
                "committed_due_date": "", "scheduled_start": "", "estimated_completion": "",
                "followup_date": "", "internal_notes": "", "version": 1}
        data.update(changes)
        payload = {f"{self.item.pk}-{key}": value for key, value in data.items()}
        return {"action": "edit", "item_id": str(self.item.pk), **payload}

    def test_full_edit_saves_notes_dates_and_audit(self):
        self.login()
        response = self.client.post(self.url, self.edit_payload(internal_notes="Call engineer", followup_date="2026-11-03"))
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.internal_notes, "Call engineer")
        self.assertEqual(str(self.item.followup_date), "2026-11-03")
        self.assertTrue(AuditEvent.objects.filter(work_item=self.item, changes__has_key="internal_notes").exists())

    def test_conflicting_full_edit_preserves_attempt_and_loads_current_form(self):
        self.login()
        edit_work(item_id=self.item.pk, version=1, data={"internal_notes": "Newer notes"}, actor=self.owner)
        response = self.client.post(self.url, self.edit_payload(internal_notes="Unsaved older notes"))
        self.assertContains(response, "Unsaved older notes", status_code=409)
        self.assertContains(response, "Newer notes", status_code=409)
        self.assertEqual(response.context["rows"][0]["form"].instance.version, 2)

    def test_csrf_is_required_for_queue_changes(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed"})
        self.assertEqual(response.status_code, 403)

    def test_manual_capture_can_be_retried_without_duplicates(self):
        self.login()
        data = {f"new-{key}": value for key, value in work_data().items()}
        data.update({"action": "create", "new-kind": "new", "new-submission_token": str(uuid.uuid4())})
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(WorkItem.objects.count(), 2)

    def test_invalid_update_context_is_visible_and_preserved(self):
        self.login()
        data = {f"new-{key}": value for key, value in work_data().items()}
        data.update({"action": "create", "new-kind": "update", "new-submission_token": str(uuid.uuid4())})
        response = self.client.post(self.url, data)
        self.assertContains(response, "Select a project or enter", status_code=400)
        self.assertContains(response, "Morgan Lee", status_code=400)
        self.assertEqual(WorkItem.objects.count(), 1)

    def test_filtered_search_still_shows_global_position(self):
        self.login()
        second = create_work(data=work_data(contact_full_name="Second Client"), actor=self.owner)[0]
        response = self.client.get(self.url, {"q": "Second Client"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["item"].position, 2)
        self.assertEqual(response.context["rows"][0]["item"].pk, second.pk)

    def test_bad_date_filter_does_not_crash(self):
        self.login()
        response = self.client.get(self.url, {"received_from": "bad-date"})
        self.assertContains(response, "Use a valid received date")

    def test_submitted_text_is_escaped(self):
        self.login()
        WorkItem.objects.filter(pk=self.item.pk).update(description='<script>alert("x")</script>')
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")

    @override_settings(WORK_QUEUE_ENABLED=False)
    def test_feature_disabled_until_migration_cutover(self):
        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "create"}).status_code, 404)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLQueueConcurrencyTests(TransactionTestCase):
    """Run against a dedicated PostgreSQL test DB before production enablement."""
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.user = get_user_model().objects.create_user("queue-concurrency", is_staff=True)

    def test_parallel_requests_have_unique_ids_order_and_snapshots(self):
        user_id = self.user.pk
        def submit(number):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                item, _ = create_work(data=work_data(), actor=actor, idempotency_key=f"parallel:{number}")
                return item.reference, item.queue_order, item.submission_position
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(submit, range(4)))
        self.assertEqual(len({row[0] for row in results}), 4)
        self.assertEqual(sorted(row[1] for row in results), [1, 2, 3, 4])
        self.assertEqual(sorted(row[2] for row in results), [1, 2, 3, 4])

    def test_parallel_retry_creates_one_request(self):
        user_id = self.user.pk
        def submit(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                return create_work(data=work_data(), actor=actor, idempotency_key="parallel:one")[0].pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(WorkItem.objects.count(), 1)
