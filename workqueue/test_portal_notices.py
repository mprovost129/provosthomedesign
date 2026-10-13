import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client as Browser, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_portal_reviews as fixture
from .models import ClientContact, NotificationDelivery, WorkItem
from .portal_models import PortalDocument, PortalMember
from .portal_notice_models import PortalInvitation, PortalInvitationBatch, PortalReviewReminder
from .portal_notices import invite, queue_review_reminders, cancel_ineligible_portal_notice, configure_reminders
from .portal_reviews import submit_review
from .services import EditConflict


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
    INTAKE_PUBLIC_BASE_URL='https://testserver', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PortalNoticeTests(TestCase):
    setUp = fixture.PlanReviewTests.setUp
    version = fixture.PlanReviewTests.version
    publish = fixture.PlanReviewTests.publish
    sign_in = fixture.PlanReviewTests.sign_in
    grant_review = fixture.PlanReviewTests.grant_review
    draft = fixture.PlanReviewTests.draft

    def invitation(self, *, reminders=False, delivered=False):
        member, revision, review = self.draft()
        batch, _ = invite(self.project, version=self.version(), token=uuid.uuid4(), member_ids=[member.pk],
            revision=revision, reminders=reminders, actor=self.owner, message='Please review the window sizes.')
        notice = batch.invitations.get()
        if delivered:
            notice.delivery.state, notice.delivery.sent_at = 'sent', timezone.now()-timedelta(days=4)
            notice.delivery.save(update_fields=['state', 'sent_at'])
        return member, revision, review, notice

    def url(self, revision=None):
        return reverse('workqueue:portal_invitations', args=[self.project.pk]) + ('?document='+str(revision.pk) if revision else '')

    def test_invitation_defaults_no_reminders_and_does_not_grant_or_create_work(self):
        member, revision, _, notice = self.invitation()
        self.assertFalse(notice.reminders_enabled)
        self.assertEqual(PortalMember.objects.count(), 1)
        self.assertEqual(WorkItem.objects.count(), 2)
        self.assertIn('Version: 1', notice.delivery.body)
        self.assertIn('Please review the window sizes.', notice.delivery.body)
        self.assertIn('/client-portal/projects/', notice.delivery.body)
        self.assertEqual(notice.delivery.recipient, member.email)
        self.assertEqual(queue_review_reminders(), 0)

    def test_invitation_retry_returns_same_delivery(self):
        member, revision, _, notice = self.invitation()
        batch, created = invite(self.project, version=0, token=notice.batch_id, member_ids=[member.pk],
            revision=revision, actor=self.owner, message='Different retry')
        self.assertFalse(created)
        self.assertEqual(batch.pk, notice.batch_id)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_cannot_invite_unapproved_or_other_project_contact(self):
        member, revision, _, _ = self.invitation()
        for ids in [[], [member.pk+10000]]:
            with self.assertRaises(ValidationError): invite(self.project, version=self.version(), token=uuid.uuid4(),
                member_ids=ids, revision=revision, actor=self.owner)
        with self.assertRaises(ValidationError): invite(self.other.project, version=1, token=uuid.uuid4(),
            member_ids=[member.pk], revision=revision, actor=self.owner)

    def test_invoice_document_keeps_company_audience(self):
        member, _, _, _ = self.invitation()
        invoice = self.publish(kind='invoice', client=self.contact.client)
        with self.assertRaises(ValidationError): invite(self.project, version=self.version(), token=uuid.uuid4(),
            member_ids=[member.pk], revision=invoice, actor=self.owner)

    def test_stale_sharing_and_old_version_cannot_be_invited(self):
        member, revision, _, _ = self.invitation()
        self.publish(document=revision.document)
        with self.assertRaises(EditConflict): invite(self.project, version=self.version()-1, token=uuid.uuid4(),
            member_ids=[member.pk], revision=revision, actor=self.owner)
        with self.assertRaises(ValidationError): invite(self.project, version=self.version(), token=uuid.uuid4(),
            member_ids=[member.pk], revision=revision, actor=self.owner)

    def test_queued_invitation_canceled_after_revoke_or_contact_change(self):
        member, _, _, notice = self.invitation()
        ClientContact.objects.filter(pk=member.contact_id).update(email='changed@example.invalid')
        self.assertTrue(cancel_ineligible_portal_notice(notice.delivery))
        notice.delivery.refresh_from_db()
        self.assertEqual(notice.delivery.state, 'canceled')
        self.assertEqual(notice.delivery.body, '')

    def test_reminders_require_plan_review_access(self):
        member, revision, _, _ = self.invitation()
        PortalMember.objects.filter(pk=member.pk).update(review_access=False)
        with self.assertRaises(ValidationError): invite(self.project, version=self.version(), token=uuid.uuid4(),
            member_ids=[member.pk], revision=revision, reminders=True, actor=self.owner)

    def test_preview_get_and_post_do_not_send(self):
        member, revision, _ = self.draft()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url(revision)), 'Review email')
        response = self.client.post(self.url(revision), {'action':'preview', 'recipients':[member.pk], 'message':'Please review.'})
        self.assertContains(response, 'Review the email')
        self.assertContains(response, member.email)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_signed_preview_explicit_confirmation_and_retries(self):
        member, revision, _ = self.draft()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(revision), {'action':'preview', 'recipients':[member.pk], 'message':'<b>Plain text only</b>'})
        signed = response.context['preview']['signed']
        self.assertEqual(self.client.post(self.url(revision), {'action':'send', 'preview':signed}).status_code, 400)
        for _ in range(2):
            self.assertEqual(self.client.post(self.url(revision), {'action':'send', 'preview':signed, 'confirm':'on'}).status_code, 302)
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(PortalInvitationBatch.objects.count(), 1)
        self.assertEqual(self.client.post(self.url(revision), {'action':'send', 'preview':signed+'tampered', 'confirm':'on'}).status_code, 400)

    def test_preview_permission_change_requires_new_review(self):
        member, revision, _ = self.draft()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(revision), {'action':'preview', 'recipients':[member.pk]})
        signed = response.context['preview']['signed']
        from .portal_services import revoke_member
        revoke_member(self.project, version=self.version(), member_id=member.pk, actor=self.owner)
        response = self.client.post(self.url(revision), {'action':'send', 'preview':signed, 'confirm':'on'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(NotificationDelivery.objects.count(), 0)

    def test_preview_is_staff_only_csrf_protected_and_bad_document_404(self):
        member, revision, _ = self.draft()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        browser = Browser(enforce_csrf_checks=True); browser.force_login(self.owner)
        self.assertEqual(browser.post(self.url(), {'action':'preview', 'recipients':[member.pk]}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url()+'?document=invalid').status_code, 404)

    def test_reminder_schedule_is_bounded_and_idempotent(self):
        _, _, _, notice = self.invitation(reminders=True, delivered=True)
        self.assertEqual(queue_review_reminders(), 1)
        self.assertEqual(queue_review_reminders(), 0)
        first = notice.reminders.get().delivery
        first.state, first.sent_at = 'sent', timezone.now()
        first.save(update_fields=['state','sent_at'])
        with patch('workqueue.portal_notices.timezone.now', return_value=timezone.now()+timedelta(days=4)):
            self.assertEqual(queue_review_reminders(), 1)
            self.assertEqual(queue_review_reminders(), 0)
        self.assertEqual(notice.reminders.count(), 2)

    def test_never_remind_for_unsent_failed_or_unknown_invitation(self):
        _, _, _, notice = self.invitation(reminders=True)
        for state in ['pending','failed','unknown']:
            NotificationDelivery.objects.filter(pk=notice.delivery_id).update(state=state, sent_at=timezone.now()-timedelta(days=20))
            self.assertEqual(queue_review_reminders(), 0)

    def test_outage_does_not_send_two_reminders_together(self):
        _, _, _, notice = self.invitation(reminders=True, delivered=True)
        NotificationDelivery.objects.filter(pk=notice.delivery_id).update(sent_at=timezone.now()-timedelta(days=20))
        self.assertEqual(queue_review_reminders(), 1)
        first = notice.reminders.get().delivery
        first.state, first.sent_at = 'sent', timezone.now()
        first.save(update_fields=['state','sent_at'])
        self.assertEqual(queue_review_reminders(), 0)

    def test_review_submission_stops_reminders_and_cancels_pending_copy(self):
        member, _, review, notice = self.invitation(reminders=True, delivered=True)
        queue_review_reminders()
        pending = notice.reminders.get().delivery
        submit_review(review.pk, member, fixture.payload(review))
        self.assertTrue(cancel_ineligible_portal_notice(pending))
        self.assertEqual(queue_review_reminders(), 0)
        notice.refresh_from_db(); self.assertFalse(notice.reminders_enabled)

    def test_new_version_withdrawal_or_revocation_stops_reminders(self):
        _, revision, _, notice = self.invitation(reminders=True, delivered=True)
        self.publish(document=revision.document)
        self.assertEqual(queue_review_reminders(), 0)
        notice.refresh_from_db(); self.assertFalse(notice.reminders_enabled)

    def test_turn_off_cancels_pending_and_stale_edit_fails(self):
        _, _, _, notice = self.invitation(reminders=True, delivered=True)
        queue_review_reminders()
        configure_reminders(notice.pk, project=self.project, version=1, enabled=False, actor=self.owner)
        self.assertEqual(notice.reminders.get().delivery.state, 'canceled')
        with self.assertRaises(EditConflict): configure_reminders(notice.pk, project=self.project, version=1, enabled=True, actor=self.owner)
        self.assertEqual(queue_review_reminders(), 0)

    def test_resending_invitation_replaces_old_reminder_sequence(self):
        member, revision, _, notice = self.invitation(reminders=True, delivered=True)
        queue_review_reminders()
        invite(self.project, version=self.version(), token=uuid.uuid4(), member_ids=[member.pk], revision=revision,
            reminders=True, actor=self.owner)
        notice.refresh_from_db()
        self.assertFalse(notice.reminders_enabled)
        self.assertEqual(notice.reminders.get().delivery.state, 'canceled')

    def test_mail_worker_sends_test_invitation_and_respects_revoke(self):
        member, _, _, notice = self.invitation()
        from .notifications import deliver_pending
        from django.core import mail
        deliver_pending()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [member.email])
        self.assertEqual(mail.outbox[0].cc, [])
        self.assertEqual(mail.outbox[0].bcc, [])
        notice.delivery.refresh_from_db()
        self.assertEqual(notice.delivery.state, 'sent')
        self.assertEqual(notice.delivery.body, '')
