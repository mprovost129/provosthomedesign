"""Draft/privacy, immutable submission, exact identity, and queue/outbox integration."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client as Browser, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import Client, ClientContact, NotificationDelivery, QueueState, Submission, WorkItem
from .portal_models import PortalDocument, PortalMember
from .portal_review_models import PlanReview
from .portal_reviews import clean_payload, mark_reviewed, save_review, start_review, submit_review
from .portal_services import grant_member
from .services import EditConflict, create_work, edit_work
from . import test_portal as portal_fixture
from .tests import work_data


def payload(review, **changes):
    data = dict(version=review.version, summary='Please widen the kitchen window.',
        geometry=[{'page': 1, 'width': 612, 'height': 792, 'rotation': 0}],
        annotations=[{'id': str(uuid.uuid4()), 'page': 1, 'x': .3, 'y': .4, 'text': 'Change to a six foot window.'}])
    data.update(changes)
    return data


class PlanReviewTests(TestCase):
    setUp = portal_fixture.PortalTests.setUp
    version = portal_fixture.PortalTests.version
    grant = portal_fixture.PortalTests.grant
    sign_in = portal_fixture.PortalTests.sign_in
    publish = portal_fixture.PortalTests.publish
    project_url = portal_fixture.PortalTests.project_url
    def grant_review(self):
        return grant_member(self.project, version=self.version(), contact_id=self.contact.pk,
            financial_access=False, review_access=True, actor=self.owner)

    def draft(self):
        member = self.grant_review()
        revision = self.publish()
        record = start_review(member, revision, uuid.uuid4())
        self.sign_in()
        return member, revision, record

    def url(self, name, record):
        return reverse('workqueue:'+name, args=[self.project.pk, record.pk])

    def test_existing_grant_does_not_allow_review(self):
        member = self.grant()
        revision = self.publish()
        with self.assertRaises(PermissionDenied): start_review(member, revision, uuid.uuid4())
        self.sign_in()
        self.assertNotContains(self.client.get(self.project_url()), 'Review plan')

    def test_start_get_does_not_write(self):
        self.grant_review(); revision = self.publish(); self.sign_in()
        response = self.client.get(reverse('workqueue:start_plan_review', args=[self.project.pk, revision.pk]))
        self.assertContains(response, 'Start review')
        self.assertEqual(PlanReview.objects.count(), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_start_retries_and_concurrent_tab_share_one_draft(self):
        member, revision, record = self.draft()
        self.assertEqual(start_review(member, revision, record.pk).pk, record.pk)
        self.assertEqual(start_review(member, revision, uuid.uuid4()).pk, record.pk)
        self.assertEqual(PlanReview.objects.count(), 1)

    def test_draft_only_autosaves_no_queue_no_mail(self):
        member, _, record = self.draft()
        before = QueueState.objects.get(pk=1).last_reference_number
        response = self.client.post(self.url('save_plan_review', record), json.dumps(payload(record)), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db(); self.assertEqual(record.version, 2)
        self.assertEqual(len(record.annotations), 1)
        self.assertEqual(QueueState.objects.get(pk=1).last_reference_number, before)
        self.assertEqual(NotificationDelivery.objects.count(), 0)
        self.assertEqual(WorkItem.objects.count(), 2)
        self.assertEqual(self.client.get(self.url('portal_review', record))['Cache-Control'], 'no-store, private')

    def test_stale_autosave_keeps_first_copy(self):
        member, _, record = self.draft()
        data = payload(record)
        save_review(record.pk, member, data)
        with self.assertRaises(EditConflict): save_review(record.pk, member, dict(data, summary='Another tab'))
        record.refresh_from_db(); self.assertEqual(record.summary, data['summary'])

    def test_submit_has_exact_company_queue_snapshot_and_two_receipts(self):
        member, revision, record = self.draft()
        record, created = submit_review(record.pk, member, payload(record))
        self.assertTrue(created)
        item = record.work_item
        self.assertEqual(item.project_id, self.project.pk)
        self.assertEqual(item.client_contact_id, self.contact.pk)
        self.assertEqual(item.kind, 'update'); self.assertEqual(item.source, 'Client portal')
        self.assertEqual(item.submission_position, 3)
        self.assertEqual(item.status, 'new'); self.assertIn('six foot', item.description)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(Client.objects.count(), 2)
        for notice in NotificationDelivery.objects.all():
            self.assertIn('Change to a six foot window.', notice.body)
            self.assertIn('Version 1', notice.body)
            self.assertIn('Open review:', notice.body)
        self.assertContains(self.client.get(self.url('portal_review', record)), item.reference)
        self.assertEqual(Submission.objects.get(work_item=item).channel, 'portal')

    def test_submit_retry_returns_same_request_and_frozen_notes(self):
        member, _, record = self.draft()
        data = payload(record)
        first, _ = submit_review(record.pk, member, data)
        second, created = submit_review(record.pk, member, dict(data, summary='Uncommitted late change'))
        self.assertFalse(created); self.assertEqual(first.work_item_id, second.work_item_id)
        second.refresh_from_db(); self.assertEqual(second.summary, data['summary'])
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(WorkItem.objects.count(), 3)
        with self.assertRaises(EditConflict): save_review(record.pk, member, data)

    def test_submitted_review_can_start_a_separate_new_request(self):
        member, revision, record = self.draft()
        submit_review(record.pk, member, payload(record))
        new = start_review(member, revision, uuid.uuid4())
        self.assertNotEqual(new.pk, record.pk)
        submit_review(new.pk, member, payload(new))
        self.assertEqual(WorkItem.objects.count(), 4)

    def test_revocation_blocks_save_submit_and_download(self):
        member, _, record = self.draft()
        PortalMember.objects.filter(pk=member.pk).update(revoked_at=timezone.now())
        with self.assertRaises(PermissionDenied): save_review(record.pk, member, payload(record))
        with self.assertRaises(PermissionDenied): submit_review(record.pk, member, payload(record))
        self.assertEqual(self.client.get(self.url('portal_review', record)).status_code, 404)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_disable_review_access_preserves_read_only_draft(self):
        member, _, record = self.draft()
        save_review(record.pk, member, payload(record))
        PortalMember.objects.filter(pk=member.pk).update(review_access=False)
        with self.assertRaises(PermissionDenied): submit_review(record.pk, member, payload(record))
        self.assertContains(self.client.get(self.url('portal_review', record)), 'draft is read-only')

    def test_reapproval_does_not_silently_reuse_old_draft(self):
        member, revision, record = self.draft()
        PortalMember.objects.filter(pk=member.pk).update(granted_at=timezone.now()+timedelta(seconds=2))
        with self.assertRaises(PermissionDenied): save_review(record.pk, member, payload(record))
        self.assertNotEqual(start_review(member, revision, uuid.uuid4()).pk, record.pk)

    def test_changed_crm_email_company_or_archived_contact_blocks_edits(self):
        member, _, record = self.draft()
        for change in [{'email':'new@example.invalid'}, {'archived_at':timezone.now()}, {'client':self.other.client_contact.client}]:
            with transaction.atomic():
                ClientContact.objects.filter(pk=self.contact.pk).update(**change)
                with self.assertRaises(PermissionDenied): submit_review(record.pk, member, payload(record))
                transaction.set_rollback(True)
        self.assertEqual(WorkItem.objects.count(), 2)

    def test_merging_company_blocks_queue_reassignment(self):
        member, _, record = self.draft()
        Client.objects.filter(pk=member.client_id).update(merged_into=self.other.client_contact.client)
        with self.assertRaises(PermissionDenied): submit_review(record.pk, member, payload(record))

    def test_second_billing_company_with_shared_email_cannot_read_other_review(self):
        member, _, record = self.draft()
        PortalMember.objects.filter(pk=member.pk).update(client=self.other.client_contact.client, contact=self.other.client_contact)
        self.assertEqual(self.client.get(self.url('portal_review', record)).status_code, 404)

    def test_another_project_or_identity_cannot_fetch_reviews(self):
        member, revision, record = self.draft()
        self.sign_in(self.other.client_contact.email)
        self.assertEqual(self.client.get(self.url('portal_review', record)).status_code, 404)
        with self.assertRaises(PermissionDenied): start_review(member, self.publish(project=self.other.project), uuid.uuid4())

    def test_withdrawn_document_blocks_review_and_saved_report(self):
        member, revision, record = self.draft()
        PortalDocument.objects.filter(pk=revision.document_id).update(withdrawn_at=timezone.now())
        with self.assertRaises(PermissionDenied): submit_review(record.pk, member, payload(record))
        self.assertEqual(self.client.get(self.url('portal_review', record)+'?report=1').status_code, 404)

    def test_new_release_does_not_migrate_old_pins(self):
        member, revision, record = self.draft()
        save_review(record.pk, member, payload(record))
        latest = self.publish(document=revision.document)
        self.assertContains(self.client.get(self.url('portal_review', record)), 'earlier version')
        record.refresh_from_db(); self.assertEqual(record.document_version_id, revision.pk)
        self.assertEqual(latest.plan_reviews.count(), 0)

    def test_drive_links_and_invoices_cannot_be_marked_up(self):
        member = self.grant_review()
        for revision in [self.publish(url='https://drive.google.com/file/d/EXTERNAL/view'), self.publish(kind='invoice', client=self.contact.client)]:
            with self.assertRaises(PermissionDenied): start_review(member, revision, uuid.uuid4())

    def test_empty_or_blank_pin_submit_rolls_back_and_preserves_draft(self):
        member, _, record = self.draft()
        for data in [payload(record, summary='', annotations=[]), payload(record, annotations=[dict(payload(record)['annotations'][0], text=' ')])]:
            with self.assertRaises(ValidationError): submit_review(record.pk, member, data)
        record.refresh_from_db(); self.assertEqual(record.version, 1)
        self.assertIsNone(record.submitted_at); self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_general_notes_without_viewer_can_submit(self):
        member, _, record = self.draft()
        response = self.client.post(self.url('submit_plan_review', record), {'version':1, 'summary':'Change garage dimensions.'})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db(); self.assertIsNotNone(record.submitted_at)
        self.assertEqual(record.annotations, [])

    def test_bad_and_oversize_json_returns_validation_error(self):
        _, _, record = self.draft()
        for value in ['not JSON', 'x'*240001, json.dumps({'version':1})]:
            response = self.client.post(self.url('save_plan_review', record), value, content_type='application/json')
            self.assertEqual(response.status_code, 400)

    def test_review_payload_rejects_nonfinite_duplicate_or_invalid_geometry(self):
        _, _, record = self.draft()
        valid = payload(record); pin = valid['annotations'][0]
        cases = [dict(valid, summary='x'*6001), dict(valid, version=True), dict(valid, annotations=[pin]*101),
            dict(valid, annotations=[dict(pin, x=float('nan'))]), dict(valid, annotations=[dict(pin, page=2)]),
            dict(valid, annotations=[dict(pin, y=-.1)]), dict(valid, annotations=[dict(pin, id='not-uuid')]),
            dict(valid, annotations=[pin, pin]), dict(valid, geometry=[dict(valid['geometry'][0], rotation=45)]),
            dict(valid, annotations=[dict(pin, text='\x00')]), dict(valid, annotations=[dict(pin, text='x'*1501)]),
            dict(valid, geometry=[dict(valid['geometry'][0], width=float('inf'))]), dict(valid, admin_note='private')]
        for data in cases:
            with self.assertRaises(ValidationError): clean_payload(data)

    def test_outbox_failure_rolls_back_whole_submission(self):
        member, _, record = self.draft()
        with patch('workqueue.notifications.queue_receipts', side_effect=RuntimeError('outbox test')):
            with self.assertRaises(RuntimeError): submit_review(record.pk, member, payload(record))
        record.refresh_from_db(); self.assertIsNone(record.submitted_at)
        self.assertEqual(WorkItem.objects.count(), 2)

    def test_staff_cannot_read_unsubmitted_client_draft(self):
        _, _, record = self.draft(); self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url('staff_plan_review', record)).status_code, 404)
        self.assertEqual(self.client.get(self.url('staff_review_plan', record)).status_code, 404)

    def test_staff_reviews_and_queue_link_read_flag_are_separate_from_job_status(self):
        member, _, record = self.draft(); record, _ = submit_review(record.pk, member, payload(record))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('workqueue:queue')), 'Plan review · 1 note')
        self.assertContains(self.client.get(self.url('staff_plan_review', record)), 'Mark review read')
        response = self.client.post(self.url('staff_plan_review', record), {'version':record.version})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db(); self.assertIsNotNone(record.reviewed_at)
        self.assertEqual(record.work_item.status, 'new'); self.assertEqual(NotificationDelivery.objects.count(), 2)

    def test_staff_view_permission_does_not_allow_marking_reviewed(self):
        from django.contrib.auth.models import Permission
        member, _, record = self.draft(); record, _ = submit_review(record.pk, member, payload(record))
        viewer = get_user_model().objects.create_user('review-viewer', is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem', content_type__app_label='workqueue'))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url('staff_plan_review', record)).status_code, 200)
        self.assertEqual(self.client.post(self.url('staff_plan_review', record), {'version':record.version}).status_code, 403)

    def test_review_report_is_private_pdf_and_html_notes_are_escaped(self):
        member, _, record = self.draft()
        record, _ = submit_review(record.pk, member, payload(record, summary='<script>alert(1)</script>'))
        self.assertNotContains(self.client.get(self.url('portal_review', record)), '<script>alert(1)</script>')
        report = self.client.get(self.url('portal_review', record)+'?report=1')
        self.assertEqual(report['Content-Type'], 'application/pdf'); self.assertTrue(report.content.startswith(b'%PDF'))
        self.assertEqual(report['Cache-Control'], 'no-store, private')

    def test_csrf_is_required_for_draft_and_submission(self):
        _, _, record = self.draft()
        browser = Browser(enforce_csrf_checks=True); self.sign_in(browser=browser)
        for action in ['save_plan_review', 'submit_plan_review']:
            self.assertEqual(browser.post(self.url(action, record), json.dumps(payload(record)), content_type='application/json').status_code, 403)

    def test_plan_review_submission_pair_database_constraint(self):
        _, _, record = self.draft()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlanReview.objects.filter(pk=record.pk).update(submitted_at=timezone.now())

    def test_client_cannot_inject_staff_contact_through_creation(self):
        member, _, _ = self.draft()
        with self.assertRaises(PermissionDenied): create_work(data=work_data(), actor=self.owner, portal_member=member)
        with self.assertRaises(PermissionDenied): create_work(data=work_data(), actor=None, portal_member=member)

    def test_queue_link_uses_original_plan_project_after_staff_relinks_job(self):
        member, _, record = self.draft(); record, _ = submit_review(record.pk, member, payload(record))
        item = record.work_item
        edit_work(item_id=item.pk, version=item.version, data={'project':self.other.project}, actor=self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('workqueue:queue')), self.url('staff_plan_review', record))

    def test_review_confirmation_tracks_reordering_and_closed_status(self):
        member, _, record = self.draft(); record, _ = submit_review(record.pk, member, payload(record))
        url = self.url('portal_review', record)
        self.assertContains(self.client.get(url), 'Current queue position: 3.')
        WorkItem.objects.filter(pk=record.work_item_id).update(queue_order=1)
        WorkItem.objects.filter(pk__in=[self.item.pk, self.other.pk]).update(queue_order=5)
        self.assertContains(self.client.get(url), 'Current queue position: 1.')
        WorkItem.objects.filter(pk=record.work_item_id).update(status='completed')
        response = self.client.get(url)
        self.assertContains(response, 'Current status: Completed.')
        self.assertNotContains(response, 'Current queue position:')


class PlanReviewConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        portal_fixture.PortalTests.setUp(self)
    version = portal_fixture.PortalTests.version
    publish = portal_fixture.PortalTests.publish

    def test_two_concurrent_submissions_create_one_connected_job_and_receipt_pair(self):
        from django.db import connection, connections
        if connection.vendor != 'postgresql': self.skipTest('Production row lock requires PostgreSQL.')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        member = grant_member(self.project, version=self.version(), contact_id=self.contact.pk,
            financial_access=False, review_access=True, actor=self.owner)
        record = start_review(member, self.publish(), uuid.uuid4())
        data = payload(record)
        barrier = Barrier(2)
        def submit_once():
            try:
                barrier.wait(timeout=10)
                result, created = submit_review(record.pk, member, data)
                return result.work_item_id, created
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _:submit_once(), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(result[1] for result in results), 1)
        self.assertEqual(WorkItem.objects.count(), 3)
        self.assertEqual(NotificationDelivery.objects.count(), 2)

    def test_two_concurrent_draft_starts_share_one_draft(self):
        from django.db import connection, connections
        if connection.vendor != 'postgresql': self.skipTest('Production row lock requires PostgreSQL.')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        member = grant_member(self.project, version=self.version(), contact_id=self.contact.pk,
            financial_access=False, review_access=True, actor=self.owner)
        revision = self.publish()
        barrier = Barrier(2)
        def start_once():
            try:
                barrier.wait(timeout=10)
                return start_review(member, revision, uuid.uuid4()).pk
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: ids = list(pool.map(lambda _:start_once(), range(2)))
        self.assertEqual(ids[0], ids[1]); self.assertEqual(PlanReview.objects.count(), 1)

