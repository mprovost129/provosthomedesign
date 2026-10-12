"""Regression coverage for billing ownership and shared-mailbox history."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .crm import merge_billing_preview, merge_clients, undo_merge
from .crm_overview import client_appointments, timeline
from .freshbooks import link_client
from .freshbooks_models import FreshBooksClientLink
from .models import Appointment, NotificationDelivery
from .services import EditConflict, create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True)
class BillingMergeTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser("merge-review", password=None)
        self.first = create_work(data=work_data(contact_email="first@example.invalid"), actor=self.actor)[0]
        self.second = create_work(data=work_data(contact_email="second@example.invalid"), actor=self.actor)[0]
        self.source = self.first.client_contact.client
        self.target = self.second.client_contact.client
        self.link = FreshBooksClientLink.objects.create(
            client=self.source, account_id="TEST", customer_id=10, label="Original billing client", actor=self.actor)
        self.client.force_login(self.actor)

    def merge(self, **kwargs):
        return merge_clients(source_id=self.source.pk, source_version=self.source.version,
                             target_id=self.target.pk, target_version=self.target.version, actor=self.actor, **kwargs)

    def undo(self, target):
        event = target.events.get(action="Client records merged")
        return undo_merge(client_id=target.pk, version=target.version, event_id=event.pk, actor=self.actor)

    def test_connection_moves_and_undo_restores_original_ownership(self):
        target = self.merge()
        self.link.refresh_from_db()
        self.assertEqual(self.link.client_id, target.pk)
        self.assertEqual(target.billing_links.get(account_id="TEST").customer_id, 10)
        self.undo(target)
        self.link.refresh_from_db()
        self.assertEqual(self.link.client_id, self.source.pk)
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_conflicting_connections_reject_merge_without_moving_contacts(self):
        FreshBooksClientLink.objects.create(client=self.target, account_id="TEST", customer_id=11, label="Other")
        with self.assertRaisesMessage(ValidationError, "different FreshBooks customers"):
            self.merge()
        self.source.refresh_from_db()
        self.first.refresh_from_db()
        self.link.refresh_from_db()
        self.assertIsNone(self.source.merged_into_id)
        self.assertEqual(self.first.client_contact.client_id, self.source.pk)
        self.assertEqual(self.link.client_id, self.source.pk)

    def test_different_businesses_preserved_and_undo_moves_only_source_connection(self):
        retained = FreshBooksClientLink.objects.create(client=self.target, account_id="SECOND", customer_id=10, label="Other business")
        target = self.merge()
        self.assertEqual(target.billing_links.count(), 2)
        self.undo(target)
        retained.refresh_from_db()
        self.link.refresh_from_db()
        self.assertEqual(retained.client_id, self.target.pk)
        self.assertEqual(self.link.client_id, self.source.pk)

    def test_billing_changes_invalidate_preview_and_undo_even_without_client_version_change(self):
        reviewed = merge_billing_preview(self.source, self.target)
        self.link.customer_id = 12
        self.link.save(update_fields=["customer_id"])
        with self.assertRaises(EditConflict):
            self.merge(reviewed_billing=reviewed)
        target = self.merge()
        self.link.customer_id = 13
        self.link.save(update_fields=["customer_id"])
        with self.assertRaises(EditConflict):
            self.undo(target)

    def test_signed_browser_preview_discloses_connections_and_rejects_changed_billing(self):
        url = reverse("workqueue:client_detail", args=[self.source.pk])
        data = {"action": "preview_merge", "merge-version": self.source.version, "merge-target": self.target.pk}
        response = self.client.post(url, data)
        self.assertContains(response, "Original billing client")
        self.assertContains(response, "Customer 10")
        token = response.context["merge_signature"]
        self.link.customer_id = 14
        self.link.save(update_fields=["customer_id"])
        response = self.client.post(url, {**data, "action": "merge", "signature": token})
        self.assertEqual(response.status_code, 409)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.merged_into_id)

    def test_relink_invalidates_undo_and_audits_client_change(self):
        target = self.merge()
        with patch("workqueue.freshbooks.account_id", return_value="TEST"), patch(
                "workqueue.freshbooks.remote_client", return_value={"organization": "Reviewed customer"}):
            link_client(target, 20, self.actor)
        target.refresh_from_db()
        self.assertTrue(target.events.filter(action="Billing connection updated").exists())
        with self.assertRaises(EditConflict):
            self.undo(target)

    def test_relink_resolves_merge_that_happened_during_provider_lookup(self):
        def provider_lookup(customer_id):
            self.merge()
            return {"organization": "Reviewed customer"}

        with patch("workqueue.freshbooks.account_id", return_value="TEST"), patch(
                "workqueue.freshbooks.remote_client", side_effect=provider_lookup):
            linked = link_client(self.source, 20, self.actor)
        self.assertEqual(linked.client_id, self.target.pk)
        self.assertFalse(self.source.billing_links.exists())

    def test_accepted_proposal_can_prepare_invoice_after_merge_without_relinking(self):
        import uuid
        from .freshbooks import queue_invoice
        from .pm_services import save_proposal, send_proposal, respond
        from .test_pm import proposal_data

        proposal = save_proposal(self.first.project_id, self.source.pk, proposal_data(), uuid.uuid4(), 0, self.actor)
        revision = send_proposal(proposal.pk, proposal.version, self.first.client_contact_id, uuid.uuid4(), self.actor)
        respond(revision.invitation.pk, {"action": "accept", "full_name": "Test client", "agreement": True})
        proposal.refresh_from_db()
        original_snapshot = dict(revision.snapshot)
        target = self.merge()
        with patch("workqueue.freshbooks.account_id", return_value="TEST"):
            invoice = queue_invoice(proposal.pk, proposal.version, self.actor, confirm=True)
        self.assertEqual(invoice.customer_id, 10)
        self.assertEqual(invoice.payload["invoice"]["customerid"], 10)
        revision.refresh_from_db()
        self.assertEqual(revision.snapshot, original_snapshot)
        self.assertEqual(target.billing_links.get(account_id="TEST").pk, self.link.pk)

    def test_legacy_merge_undo_requires_review_when_billing_snapshot_is_missing(self):
        target = self.merge()
        event = target.events.get(action="Client records merged")
        event.changes.pop("billing_before")
        event.changes.pop("billing_after")
        event.save(update_fields=["changes"])
        with self.assertRaises(EditConflict):
            self.undo(target)
        self.link.refresh_from_db()
        self.assertEqual(self.link.client_id, target.pk)


@override_settings(WORK_QUEUE_ENABLED=True)
class SharedAppointmentHistoryTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser("appointment-review", password=None)
        self.first = create_work(data=work_data(company="Company A"), actor=self.actor)[0]
        self.second = create_work(data=work_data(company="Company B"), actor=self.actor)[0]
        self.a = self.first.client_contact.client
        self.b = self.second.client_contact.client
        self.client.force_login(self.actor)

    def appointment(self, project, key, email="morgan@example.com"):
        now = timezone.now()
        return Appointment.objects.create(project=project, email=email, full_name="Shared contact", phone="",
            purpose=key, starts_at=now, ends_at=now + timedelta(minutes=30),
            reserved_until=now + timedelta(minutes=60), local_date=timezone.localdate(),
            calendar_id="test", idempotency_key=key)

    def test_other_company_appointment_and_notice_are_absent_from_both_history_views(self):
        appointment = self.appointment(self.first.project, "Company A appointment")
        NotificationDelivery.objects.create(appointment=appointment, appointment_event="confirmed",
            recipient_kind="client", recipient=appointment.email, subject="Company A appointment notice")
        self.assertIn(appointment, client_appointments(self.a))
        self.assertNotIn(appointment, client_appointments(self.b))
        rows = timeline(self.b, 1).object_list
        self.assertFalse(any("Company A appointment" in row["body"] for row in rows))
        response = self.client.get(reverse("workqueue:client_detail", args=[self.b.pk]))
        self.assertNotContains(response, "Company A appointment")
        self.assertFalse(response.context["appointments"])

    def test_project_history_survives_contact_email_change(self):
        appointment = self.appointment(self.first.project, "Project appointment", "old@example.invalid")
        self.assertIn(appointment, client_appointments(self.a))

    def test_unassigned_mailbox_appointments_are_explicitly_labeled(self):
        appointment = self.appointment(None, "Unassigned appointment")
        for client in (self.a, self.b):
            self.assertIn(appointment, client_appointments(client))
            rows = timeline(client, 1).object_list
            self.assertTrue(any("No project assigned; matched by contact email" in row["body"] for row in rows))
            response = self.client.get(reverse("workqueue:client_detail", args=[client.pk]))
            self.assertContains(response, "No project assigned; matched by contact email")
