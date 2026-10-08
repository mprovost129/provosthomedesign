import copy
import uuid

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase, override_settings

from .legacy_import import ImportConflict, import_snapshot
from .access import consume_email_token, mint_email_token
from .models import (Attachment, AuditEvent, EmailAccessToken, LegacyQueueImport, NotificationDelivery, Status, StatusMilestone,
                     BookingAccess, ProjectAccess, QueueState, Submission, WorkItem, WorkProject)


SOURCE = "test_sheet_source_1234"


def snapshot():
    root, child = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [{"Queue ID": root, "Request Number": "PHD-00001", "Queue Order": 10,
             "Received At": "2026-09-03T12:00:00", "Client": "Example Builder", "Contact Name": None,
             "Company": None, "Email": None, "Phone": None, "Project Name": "Lot 14", "Status": "Completed",
             "Priority": "Soon", "Intake Channel": "Legacy Form", "Description of Work": "Draw plans"},
            {"Queue ID": child, "Request Number": "PHD-00027", "Queue Order": 250,
             "Received At": "2026-10-07T10:00:00", "Client": "Example Client", "Contact Name": "Example Client",
             "Company": None, "Email": "client@example.invalid", "Phone": None, "Project Name": "Lot 14",
             "Status": "New", "Priority": "Normal", "Intake Channel": "Files and Updates",
             "Description of Work": "See attached", "First Submission Key": "upload_1"}]
    return {"version": 1, "source_id": SOURCE, "source_timezone": "America/New_York", "tables": {
        "Master Queue": rows,
        "Submissions": [{"Submission Key": "upload_1", "Queue ID": child, "Submitted At": "2026-10-07T10:00:00",
                         "Process State": "APPLIED", "Form Key": "UPDATES", "Contact Email": "client@example.invalid",
                         "Verified Respondent Email": "operator@example.invalid", "Requests Ahead": 8},
                        {"Submission Key": "removed_test", "Queue ID": str(uuid.uuid4()), "Request Number": "PHD-00030"}],
        "Answers": [{"Submission Key": "upload_1", "Question": "What changed?", "Answer Text": "<script>Text</script>"}],
        "Attachments": [{"Submission Key": "upload_1", "Attachment Key": "file_1", "File ID": "drive_file_test_1234",
                         "Received At": "2026-10-07T10:00:00", "Category": "Plans or survey; Photos or sketches"}],
        "Extra source tab": [{"Unmatched": "Preserve this too"}]}}


@override_settings(WORK_QUEUE_ENABLED=False, WORK_INTAKE_ENABLED=False, BOOKING_ENABLED=False)
class LegacyImportTests(TestCase):
    def setUp(self):
        self.source = snapshot()
        self.links = {"PHD-00027": "PHD-00001"}

    def run_import(self, apply=True, source=None):
        return import_snapshot(source or self.source, high_water=30, links=self.links, apply=apply)

    def test_dry_run_validates_and_rolls_back_every_write(self):
        result = self.run_import(apply=False)
        self.assertEqual(result["active"], 1)
        self.assertEqual(result["closed"], 1)
        for model in [WorkItem, WorkProject, Submission, Attachment, AuditEvent, LegacyQueueImport]:
            self.assertEqual(model.objects.count(), 0)
        self.assertEqual(QueueState.objects.get().last_reference_number, 0)

    def test_import_preserves_ids_order_history_missing_data_and_connections(self):
        result = self.run_import()
        root, child = WorkItem.objects.order_by("queue_order")
        self.assertEqual(str(root.pk), self.source["tables"]["Master Queue"][0]["Queue ID"])
        self.assertEqual((root.queue_order, child.queue_order), (10, 250))
        self.assertEqual(child.previous_request, root)
        self.assertEqual(child.project, root.project)
        self.assertEqual(child.submission_position, 9)
        self.assertEqual((root.company, root.contact_email, root.contact_phone), ("", "", ""))
        self.assertEqual(root.received_at.isoformat(), "2026-09-03T16:00:00+00:00")
        self.assertEqual(result["archived_orphan_submissions"], 1)
        self.assertEqual(LegacyQueueImport.objects.get().payload, self.source)
        self.assertEqual(Attachment.objects.get().categories, ["Plans or survey", "Photos or sketches"])
        self.assertEqual(QueueState.objects.get().last_reference_number, 30)
        self.assertFalse(Submission.objects.filter(owner_email="operator@example.invalid").exists())
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(EmailAccessToken.objects.count(), 0)
        self.assertEqual(ProjectAccess.objects.count(), 0)

    def test_retry_is_idempotent_and_changed_sheet_status_reconciles(self):
        self.run_import()
        self.assertEqual(self.run_import()["changed_requests"], 0)
        self.assertEqual(WorkItem.objects.count(), 2)
        self.assertEqual(Submission.objects.count(), 2)
        self.assertEqual(LegacyQueueImport.objects.count(), 1)
        revised = copy.deepcopy(self.source)
        revised["tables"]["Master Queue"][1]["Status"] = "Completed"
        self.run_import(source=revised)
        self.assertEqual(WorkItem.objects.active().count(), 0)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_shared_root_metadata_refreshes_all_connected_baselines(self):
        self.run_import()
        revised = copy.deepcopy(self.source)
        revised["tables"]["Master Queue"][0]["Project Name"] = "Lot 14 revised name"
        self.run_import(source=revised)
        self.assertEqual(WorkProject.objects.get().name, "Lot 14 revised name")
        self.assertEqual(self.run_import(source=revised)["changed_requests"], 0)

    @override_settings(WORK_QUEUE_ENABLED=True)
    def test_private_original_file_is_staff_only_and_source_answers_are_escaped(self):
        with override_settings(WORK_QUEUE_ENABLED=False):
            self.run_import()
        attachment = Attachment.objects.get()
        url = reverse("workqueue:download", args=[attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.invalid", "test-only"))
        response = self.client.get(url)
        self.assertRedirects(response, "https://drive.google.com/file/d/drive_file_test_1234/view", fetch_redirect_response=False)
        response = self.client.get(reverse("workqueue:queue"))
        self.assertContains(response, "Original Google Drive file")
        self.assertContains(response, "&lt;script&gt;Text&lt;/script&gt;")
        self.assertNotContains(response, "<script>Text</script>")

    def test_app_edit_is_never_overwritten(self):
        self.run_import()
        WorkItem.objects.filter(reference="PHD-00027").update(internal_notes="Owner edit", version=2)
        with self.assertRaises(ImportConflict):
            self.run_import()
        self.assertEqual(WorkItem.objects.get(reference="PHD-00027").internal_notes, "Owner edit")

    @override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
    def test_imported_request_can_be_completed_without_fabricating_missing_contact_details(self):
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_INTAKE_ENABLED=False):
            self.run_import()
        owner = get_user_model().objects.create_superuser('legacy-editor', 'owner@example.invalid', 'test-only')
        self.client.force_login(owner)
        item = WorkItem.objects.get(reference='PHD-00027')
        response = self.client.post(reverse('workqueue:queue'), {
            'action': 'status', 'item_id': item.pk, 'version': item.version,
            'status': Status.COMPLETED, 'client_email': 'skip',
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.COMPLETED)
        self.assertEqual((item.company, item.contact_phone), ('', ''))
        self.assertEqual(item.version, 2)
        self.assertEqual(WorkItem.objects.active().count(), 0)
        self.assertIsNone(StatusMilestone.objects.get().delivery_id)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    @override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
                       INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com')
    def test_imported_request_with_email_can_queue_completion_notice_despite_missing_phone_company(self):
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_INTAKE_ENABLED=False):
            self.run_import()
        owner = get_user_model().objects.create_superuser('legacy-notifier', 'owner@example.invalid', 'test-only')
        self.client.force_login(owner)
        item = WorkItem.objects.get(reference='PHD-00027')
        response = self.client.post(reverse('workqueue:queue'), {
            'action': 'status', 'item_id': item.pk, 'version': item.version,
            'status': Status.COMPLETED, 'client_email': 'send',
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.COMPLETED)
        self.assertEqual(NotificationDelivery.objects.get().recipient, 'client@example.invalid')
        self.assertEqual((item.company, item.contact_phone), ('', ''))

    @override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
    def test_full_queue_edit_of_imported_request_preserves_blank_intake_details(self):
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_INTAKE_ENABLED=False):
            self.run_import()
        owner = get_user_model().objects.create_superuser('legacy-full-editor', 'owner@example.invalid', 'test-only')
        self.client.force_login(owner)
        item = WorkItem.objects.get(reference='PHD-00027')
        prefix = str(item.pk) + '-'
        response = self.client.post(reverse('workqueue:queue'), {
            'action': 'edit', 'item_id': item.pk, prefix + 'version': item.version,
            prefix + 'status': Status.COMPLETED, prefix + 'priority': item.priority,
            prefix + 'queue_order': item.queue_order, prefix + 'project': item.project_id,
            prefix + 'internal_notes': 'Finished historical request.', prefix + 'client_email': 'skip',
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.COMPLETED)
        self.assertEqual(item.internal_notes, 'Finished historical request.')
        self.assertEqual((item.company, item.contact_phone), ('', ''))
        self.assertEqual(item.version, 2)

    @override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
    def test_historical_missing_email_requires_skip_and_failed_send_keeps_save_atomic(self):
        with override_settings(WORK_QUEUE_ENABLED=False, WORK_INTAKE_ENABLED=False):
            self.run_import()
        owner = get_user_model().objects.create_superuser('legacy-no-email', 'owner@example.invalid', 'test-only')
        self.client.force_login(owner)
        item = WorkItem.objects.get(reference='PHD-00001')
        url = reverse('workqueue:queue')
        data = {'action': 'status', 'item_id': item.pk, 'version': item.version,
                'status': Status.IN_PROGRESS, 'client_email': 'send'}
        response = self.client.post(url, data)
        self.assertContains(response, 'Enter a valid email address.', status_code=400)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.COMPLETED)
        self.assertEqual(item.version, 1)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(StatusMilestone.objects.count(), 0)
        data['client_email'] = 'skip'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.IN_PROGRESS)
        self.assertEqual((item.company, item.contact_email, item.contact_phone), ('', '', ''))

    def test_imported_client_booking_requires_verification_and_preserves_denials(self):
        self.run_import()
        self.assertFalse(BookingAccess.objects.exists())
        _, raw = mint_email_token("client@example.invalid")
        consume_email_token(raw)
        self.assertTrue(BookingAccess.objects.get(email="client@example.invalid").allowed)
        self.assertFalse(ProjectAccess.objects.exists())
        BookingAccess.objects.filter(email="client@example.invalid").update(allowed=False)
        _, raw = mint_email_token("client@example.invalid")
        consume_email_token(raw)
        self.assertFalse(BookingAccess.objects.get(email="client@example.invalid").allowed)
        _, raw = mint_email_token("stranger@example.invalid")
        consume_email_token(raw)
        self.assertFalse(BookingAccess.objects.filter(email="stranger@example.invalid").exists())

    def test_bad_reference_or_duplicate_order_stops_without_partial_writes(self):
        with self.assertRaises(ImportConflict):
            import_snapshot(self.source, high_water=27, links=self.links, apply=True)
        self.source["tables"]["Master Queue"][1]["Queue Order"] = 10
        with self.assertRaises(ImportConflict):
            self.run_import()
        self.assertEqual(WorkItem.objects.count(), 0)

    @override_settings(WORK_INTAKE_ENABLED=True)
    def test_live_intake_prevents_import(self):
        with self.assertRaises(ImportConflict):
            self.run_import()
