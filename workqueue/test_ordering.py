import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .access import mint_email_token
from .models import Attachment, AuditEvent, NotificationDelivery, QueueState, Status, WorkItem
from .ordering import order_snapshot, save_order
from .services import EditConflict, create_work, edit_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
    DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True)
class QueueOrderingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("ordering-owner", "owner@example.invalid", "test-only")
        self.client.force_login(self.owner)
        self.url = reverse("workqueue:ordering")
        self.a, self.b, self.c = [create_work(data=work_data(project_name=name), actor=self.owner)[0]
                                  for name in ["First", "Second", "Third"]]
        self.closed = create_work(data=work_data(status=Status.COMPLETED), actor=self.owner)[0]

    def snapshot(self):
        return self.client.get(self.url).json()["snapshot"]

    def post(self, items, snapshot=None):
        return self.client.post(self.url, json.dumps({"ordered_ids": [str(item.pk) for item in items],
            "snapshot": snapshot or self.snapshot()}), content_type="application/json")

    def test_reorder_changes_current_client_position_and_preserves_original_receipt(self):
        Attachment.objects.create(submission=self.c.submissions.get(), original_name="Survey.pdf", legacy_drive_id="demo")
        old_state = QueueState.objects.values().get(pk=1)
        response = self.post([self.c, self.a, self.b])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"saved": True, "changed": True})
        self.assertEqual(list(WorkItem.objects.active().values_list("pk", flat=True)), [self.c.pk, self.a.pk, self.b.pk])
        self.c.refresh_from_db()
        self.assertEqual(self.c.submission_position, 3)
        self.assertEqual(self.c.status, Status.NEW)
        self.assertEqual(self.c.project.name, "Third")
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.closed.refresh_from_db()
        self.assertEqual((self.closed.queue_order, self.closed.version), (4, 1))
        self.assertEqual(QueueState.objects.values().get(pk=1), old_state)
        event = self.c.audit_events.get(action="queue_reordered")
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.changes["position"], {"before": 3, "after": 1})
        # Verify the actual client lookup uses the saved order after email access.
        client = Client()
        _, raw = mint_email_token(self.c.contact_email, self.c.reference)
        self.assertEqual(client.post(reverse("workqueue:confirm_access"), {"token": raw}).status_code, 302)
        tracked = client.get(reverse("workqueue:tracking"), {"reference": self.c.reference})
        self.assertEqual(tracked.context["selected"].position, 1)
        new = create_work(data=work_data(), actor=self.owner)[0]
        self.assertEqual(WorkItem.objects.with_position().get(pk=new.pk).position, 4)
        self.assertGreater(new.queue_order, self.closed.queue_order)

    def test_unchanged_order_is_a_noop_and_signed_retry_does_not_duplicate_audits(self):
        signature = self.snapshot()
        self.assertEqual(self.post([self.a, self.b, self.c], signature).json()["changed"], False)
        self.assertFalse(AuditEvent.objects.filter(action="queue_reordered").exists())
        self.assertEqual(self.post([self.c, self.a, self.b], signature).status_code, 200)
        count = AuditEvent.objects.count()
        self.assertEqual(self.post([self.c, self.a, self.b], signature).status_code, 409)
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_tied_orders_are_unambiguous_and_every_moved_request_has_a_new_version(self):
        edit_work(item_id=self.b.pk, version=1, data={"queue_order": 1}, actor=self.owner)
        self.b.refresh_from_db()
        prior = self.b.version
        self.assertEqual(self.post([self.b, self.a, self.c]).status_code, 200)
        self.b.refresh_from_db()
        self.a.refresh_from_db()
        self.assertEqual(self.b.queue_order, 1)
        self.assertEqual(self.b.version, prior + 1)
        self.assertEqual(self.a.queue_order, 2)
        self.assertEqual(WorkItem.objects.with_position().get(pk=self.b.pk).position, 1)

    def test_full_active_queue_ignores_filters_pagination_and_keeps_updates_separate(self):
        update = create_work(data=work_data(kind="update", project=self.a.project, previous_request=self.a), actor=self.owner)[0]
        for _ in range(24):
            create_work(data=work_data(), actor=self.owner)
        response = self.client.get(self.url, {"scope": "closed", "q": "not-a-project", "page": 2})
        self.assertEqual(len(response.json()["items"]), 28)
        self.assertNotIn(str(self.closed.pk), [item["id"] for item in response.json()["items"]])
        self.assertIn(str(update.pk), [item["id"] for item in response.json()["items"]])
        page = self.client.get(reverse("workqueue:queue"), {"q": "not-a-project"})
        self.assertContains(page, "Arrange queue")
        self.assertIn("no-store", response["Cache-Control"])

    def test_partial_duplicate_closed_and_unknown_ids_cannot_reorder_anything(self):
        stranger = WorkItem(pk="12345678-1234-1234-1234-123456789abc")
        before = list(WorkItem.objects.values_list("pk", "queue_order", "version"))
        for ids in [[self.b, self.a], [self.b, self.b, self.a], [self.a, self.b, self.closed], [self.a, self.b, stranger]]:
            self.assertEqual(self.post(ids).status_code, 400)
        self.assertEqual(list(WorkItem.objects.values_list("pk", "queue_order", "version")), before)

    def test_new_job_and_status_or_manual_edits_invalidate_old_snapshot(self):
        for field, value in [("status", Status.ON_HOLD), ("internal_notes", "New staff draft"), ("queue_order", 9)]:
            signature = self.snapshot()
            self.a.refresh_from_db()
            edit_work(item_id=self.a.pk, version=self.a.version, data={field: value}, actor=self.owner)
            self.assertEqual(self.post([self.c, self.b, self.a], signature).status_code, 409)
        signature = self.snapshot()
        fourth = create_work(data=work_data(), actor=self.owner)[0]
        self.assertEqual(self.post([self.c, self.a, self.b, fourth], signature).status_code, 409)
        signature = self.snapshot()
        edit_work(item_id=fourth.pk, version=1, data={"status": Status.COMPLETED}, actor=self.owner)
        self.assertEqual(self.post([self.c, self.a, self.b], signature).status_code, 409)

    def test_bad_expired_signatures_and_malformed_bodies_never_write(self):
        for body in ["[]", "null", "{bad", '{"snapshot": 3, "ordered_ids": []}',
                     '{"snapshot": "bad", "ordered_ids": [null]}',
                     '{"snapshot": "bad", "ordered_ids": ["not-a-uuid"]}']:
            self.assertEqual(self.client.post(self.url, body, content_type="application/json").status_code, 400)
        signature = self.snapshot()
        self.assertEqual(self.post([self.c, self.a, self.b], signature + "tampered").status_code, 409)
        with patch("django.core.signing.time.time", return_value=1):
            expired = order_snapshot()["snapshot"]
        self.assertEqual(self.post([self.c, self.a, self.b], expired).status_code, 409)
        self.assertFalse(AuditEvent.objects.filter(action="queue_reordered").exists())

    def test_snapshot_is_read_only_and_does_not_expose_email_or_private_notes(self):
        self.a.internal_notes = "INTERNAL ONLY"
        self.a.save()
        count = AuditEvent.objects.count()
        response = self.client.get(self.url)
        self.assertNotContains(response, self.a.contact_email)
        self.assertNotContains(response, "INTERNAL ONLY")
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_stale_edit_forms_conflict_after_reordering_and_audit_failure_rolls_back(self):
        signature = self.snapshot()
        with patch("workqueue.ordering.AuditEvent.objects.bulk_create", side_effect=ValidationError("Could not audit")):
            self.assertEqual(self.post([self.c, self.a, self.b], signature).status_code, 400)
        self.assertEqual(signing.loads(self.snapshot(), salt="workqueue.ordering.v1"),
                         signing.loads(signature, salt="workqueue.ordering.v1"))
        self.assertEqual(self.post([self.c, self.a, self.b], signature).status_code, 200)
        with self.assertRaises(EditConflict):
            edit_work(item_id=self.a.pk, version=1, data={"status": Status.COMPLETED}, actor=self.owner)

    def test_staff_view_change_permissions_feature_flag_and_csrf_are_required(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, "{}", content_type="application/json").status_code, 403)
        reader = get_user_model().objects.create_user("ordering-reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post([self.a, self.b, self.c], "bad").status_code, 403)
        self.assertNotContains(self.client.get(reverse("workqueue:queue")), "Arrange queue")
        reader.is_staff = False
        reader.save()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.owner)
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=True):
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.put(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)


@skipUnlessDBFeature("has_select_for_update")
class OrderingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_user("ordering-concurrent", is_staff=True)
        self.items = [create_work(data=work_data(), actor=self.owner)[0] for _ in range(3)]

    def parallel(self, functions):
        def run(fn):
            close_old_connections()
            try:
                try:
                    fn()
                    return "saved"
                except EditConflict:
                    return "conflict"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, functions))

    def test_two_reorders_cannot_overwrite_each_other(self):
        signature = order_snapshot()["snapshot"]
        def save(ids):
            return lambda: save_order(ordered_ids=[str(item.pk) for item in ids], snapshot=signature, actor=self.owner)
        self.assertCountEqual(self.parallel([save(self.items[::-1]), save([self.items[1], self.items[0], self.items[2]])]),
                              ["saved", "conflict"])

    def test_concurrent_intake_is_never_dropped_or_inserted_ahead_of_saved_order(self):
        signature = order_snapshot()["snapshot"]
        results = self.parallel([
            lambda: save_order(ordered_ids=[str(item.pk) for item in self.items[::-1]], snapshot=signature, actor=self.owner),
            lambda: create_work(data=work_data(), actor=self.owner),
        ])
        self.assertEqual(results[1], "saved")
        self.assertIn(results[0], ["saved", "conflict"])
        self.assertEqual(WorkItem.objects.active().count(), 4)
        self.assertEqual(WorkItem.objects.active().last().reference, "PHD-00004")

    def test_concurrent_status_change_and_reorder_respect_versions(self):
        signature = order_snapshot()["snapshot"]
        results = self.parallel([
            lambda: save_order(ordered_ids=[str(item.pk) for item in self.items[::-1]], snapshot=signature, actor=self.owner),
            lambda: edit_work(item_id=self.items[0].pk, version=1, data={"status": Status.COMPLETED}, actor=self.owner),
        ])
        self.assertCountEqual(results, ["saved", "conflict"])
