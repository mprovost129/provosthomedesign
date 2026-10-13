import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client as Browser, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .attention import with_email_attention
from .models import ClientContact, NotificationDelivery, WorkItem
from .portal_models import PortalDocument, PortalMember
from .portal_discussion_models import ReviewMessage, ReviewNoteState
from .portal_discussions import add_reply, read_replies, set_note_state
from .portal_reviews import submit_review
from .services import EditConflict
from . import test_portal_reviews as fixture
from .test_portal_reviews import payload


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
class DiscussionTests(TestCase):
    setUp = fixture.PlanReviewTests.setUp
    version = fixture.PlanReviewTests.version
    publish = fixture.PlanReviewTests.publish
    sign_in = fixture.PlanReviewTests.sign_in
    grant_review = fixture.PlanReviewTests.grant_review
    draft = fixture.PlanReviewTests.draft
    url = fixture.PlanReviewTests.url

    def submitted(self):
        member, _, review = self.draft()
        review, _ = submit_review(review.pk, member, payload(review))
        return member, review

    def reply(self, review, member=None, **kwargs):
        return add_reply(review.pk, token=kwargs.pop('token', uuid.uuid4()), note=kwargs.pop('note', ''),
            body=kwargs.pop('body', 'Please clarify the window size.'), member=member, **kwargs)[0]

    def test_reply_keeps_original_notes_export_and_queue(self):
        member, review = self.submitted()
        before = (review.version, review.summary, review.annotations, review.geometry)
        message = self.reply(review, member)
        review.refresh_from_db()
        self.assertEqual((review.version, review.summary, review.annotations, review.geometry), before)
        self.assertEqual(WorkItem.objects.count(), 3)
        self.assertEqual(NotificationDelivery.objects.count(), 3)
        self.assertEqual(message.delivery.recipient_kind, 'owner')
        self.assertNotIn(message.body, message.delivery.body)

    def test_same_token_retry_does_not_duplicate_reply_or_email(self):
        member, review = self.submitted()
        token = uuid.uuid4()
        first = self.reply(review, member, token=token)
        again = self.reply(review, member, token=token, body='Retry body')
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(ReviewMessage.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 3)

    def test_other_author_cannot_replay_token(self):
        member, review = self.submitted()
        first = self.reply(review, member)
        with self.assertRaises(PermissionDenied):
            self.reply(review, token=first.token, actor=self.owner)

    def test_staff_reply_defaults_to_skip_email(self):
        _, review = self.submitted()
        message = self.reply(review, actor=self.owner)
        self.assertIsNone(message.delivery)
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        self.assertEqual(message.author_name, 'Provost Home Design')

    def test_explicit_staff_notification_has_private_link(self):
        _, review = self.submitted()
        message = self.reply(review, actor=self.owner, notify=True)
        self.assertEqual(message.delivery.recipient, review.author_email)
        self.assertIn('/client-portal/projects/', message.delivery.body)
        self.assertNotIn(message.body, message.delivery.body)

    def test_revocation_cancels_queued_client_reply_email(self):
        member, review = self.submitted()
        message = self.reply(review, actor=self.owner, notify=True)
        PortalMember.objects.filter(pk=member.pk).update(revoked_at=timezone.now())
        from .portal_discussions import cancel_ineligible_reply
        self.assertTrue(cancel_ineligible_reply(message.delivery))
        message.delivery.refresh_from_db()
        self.assertEqual(message.delivery.state, 'canceled')
        self.assertEqual(message.delivery.body, '')

    def test_changed_company_email_or_disabled_review_denies_reply(self):
        member, review = self.submitted()
        for field, value in [('email', 'changed@example.invalid'), ('archived_at', timezone.now())]:
            old = getattr(self.contact, field)
            ClientContact.objects.filter(pk=self.contact.pk).update(**{field: value})
            with self.assertRaises(PermissionDenied): self.reply(review, member)
            ClientContact.objects.filter(pk=self.contact.pk).update(**{field: old})
        PortalMember.objects.filter(pk=member.pk).update(review_access=False)
        with self.assertRaises(PermissionDenied): self.reply(review, member)
        with self.assertRaises(PermissionDenied): self.reply(review, actor=self.owner, notify=True)
        self.assertEqual(ReviewMessage.objects.count(), 0)

    def test_reapproved_original_identity_can_clarify_submitted_review(self):
        member, review = self.submitted()
        PortalMember.objects.filter(pk=member.pk).update(granted_at=timezone.now()+timedelta(seconds=1))
        self.reply(review, member)
        self.assertEqual(ReviewMessage.objects.count(), 1)

    def test_note_status_is_independent_and_visible_to_client(self):
        _, review = self.submitted()
        note = review.annotations[0]['id']
        state = set_note_state(review.pk, note=note, addressed=True, version=1, actor=self.owner)
        self.assertTrue(state.addressed)
        self.assertContains(self.client.get(self.url('portal_review', review)), 'Addressed')
        self.assertEqual(review.work_item.status, 'new')
        self.assertEqual(NotificationDelivery.objects.count(), 2)
        with self.assertRaises(EditConflict):
            set_note_state(review.pk, note=note, addressed=False, version=1, actor=self.owner)
        self.assertFalse(set_note_state(review.pk, note=note, addressed=False, version=2, actor=self.owner).addressed)

    def test_topic_must_belong_to_this_review(self):
        member, review = self.submitted()
        with self.assertRaises(ValidationError): self.reply(review, member, note=str(uuid.uuid4()))
        with self.assertRaises(ValidationError): set_note_state(review.pk, note='fake', addressed=True, version=1, actor=self.owner)
        self.assertEqual(ReviewMessage.objects.count(), 0)
        self.assertEqual(ReviewNoteState.objects.count(), 0)

    def test_blank_oversized_control_characters_rejected(self):
        member, review = self.submitted()
        for body in ['', ' ', 'x'*4001, 'Bad\x00body']:
            with self.assertRaises(ValidationError): self.reply(review, member, body=body)

    def test_unread_attention_survives_get_and_new_reply_after_read_snapshot(self):
        member, review = self.submitted()
        first = self.reply(review, member)
        self.client.force_login(self.owner)
        self.client.get(self.url('staff_plan_review', review))
        self.assertTrue(with_email_attention(WorkItem.objects.filter(pk=review.work_item_id)).get().response_attention)
        second = self.reply(review, member)
        read_replies(review.pk, through=first.pk, actor=self.owner)
        self.assertTrue(with_email_attention(WorkItem.objects.filter(pk=review.work_item_id)).get().response_attention)
        read_replies(review.pk, through=second.pk, actor=self.owner)
        read_replies(review.pk, through=first.pk, actor=self.owner)
        self.assertFalse(with_email_attention(WorkItem.objects.filter(pk=review.work_item_id)).get().response_attention)

    def test_staff_reply_does_not_create_unread_client_attention(self):
        _, review = self.submitted()
        self.reply(review, actor=self.owner)
        self.assertFalse(with_email_attention(WorkItem.objects.filter(pk=review.work_item_id)).get().response_attention)

    def test_invalid_read_marker_cannot_clear_future_replies(self):
        member, review = self.submitted()
        message = self.reply(review, member)
        with self.assertRaises(ValidationError): read_replies(review.pk, through=message.pk+1, actor=self.owner)

    def test_draft_and_deleted_job_cannot_receive_reply(self):
        member, _, review = self.draft()
        with self.assertRaises(ValidationError): self.reply(review, member)
        review, _ = submit_review(review.pk, member, payload(review))
        WorkItem.objects.filter(pk=review.work_item_id).update(deleted_at=timezone.now())
        with self.assertRaises(ValidationError): self.reply(review, member)

    def test_client_has_no_resolution_or_read_action(self):
        _, review = self.submitted()
        response = self.client.post(self.url('portal_review_discussion', review), {'action': 'note_state', 'addressed': '1', 'version': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReviewNoteState.objects.count(), 0)

    def test_escaped_html_and_error_preserves_message(self):
        member, review = self.submitted()
        self.reply(review, member, body='<script>bad()</script>')
        response = self.client.get(self.url('portal_review', review))
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(response, '<script>bad()</script>')
        response = self.client.post(self.url('portal_review_discussion', review), {'action':'reply', 'token':str(uuid.uuid4()), 'body':'Keep my draft', 'note':'wrong'})
        self.assertContains(response, 'Keep my draft', status_code=400)

    def test_csrf_staff_auth_and_read_only_permissions(self):
        _, review = self.submitted()
        browser = Browser(enforce_csrf_checks=True)
        self.sign_in(browser=browser)
        self.assertEqual(browser.post(self.url('portal_review_discussion', review), {'action':'reply'}).status_code, 403)
        self.assertEqual(Browser().post(self.url('staff_review_discussion', review), {}).status_code, 302)
        reader = get_user_model().objects.create_user('reader-discussion', is_staff=True)
        from django.contrib.auth.models import Permission
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        browser.force_login(reader)
        self.assertNotContains(browser.get(self.url('staff_plan_review', review)), 'Write a reply')
        self.assertEqual(browser.post(self.url('staff_review_discussion', review), {}).status_code, 403)

    def test_wrong_identity_and_project_denied(self):
        _, review = self.submitted()
        self.sign_in(self.other.client_contact.email)
        self.assertEqual(self.client.post(self.url('portal_review_discussion', review), {'action':'reply'}).status_code, 404)
        self.client.force_login(self.owner)
        wrong = reverse('workqueue:staff_review_discussion', args=[self.other.project_id, review.pk])
        self.assertEqual(self.client.post(wrong, {'action':'reply'}).status_code, 404)

    def test_withdrawn_plan_removes_discussion_from_client(self):
        member, review = self.submitted()
        PortalDocument.objects.filter(pk=review.document_version.document_id).update(withdrawn_at=timezone.now())
        self.assertEqual(self.client.get(self.url('portal_review', review)).status_code, 404)
        with self.assertRaises(PermissionDenied): self.reply(review, member)

    def test_first_reply_page_cannot_mark_unseen_later_replies_read(self):
        member, review = self.submitted()
        ReviewMessage.objects.bulk_create([ReviewMessage(review=review, member=member, body='Reply', author_name=review.author_name)
            for _ in range(51)])
        self.client.force_login(self.owner)
        response = self.client.get(self.url('staff_plan_review', review))
        through = response.context['read_through']
        self.assertEqual(through, list(review.discussion_messages.all())[49].pk)
        read_replies(review.pk, through=through, actor=self.owner)
        self.assertTrue(with_email_attention(WorkItem.objects.filter(pk=review.work_item_id)).get().response_attention)

    def test_response_filter_and_queue_link_include_client_reply(self):
        member, review = self.submitted()
        self.reply(review, member)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('workqueue:queue')+'?attention=responses')
        self.assertContains(response, 'New reply')
        self.assertContains(response, review.work_item.reference)

    def test_signin_returns_author_to_exact_review_conversation(self):
        member, review = self.submitted()
        from .access import mint_email_token
        browser = Browser()
        self.assertEqual(browser.get(self.url('portal_review', review)).status_code, 302)
        _, token = mint_email_token(member.email, destination='portal')
        response = browser.post(reverse('workqueue:confirm_access'), {'token':token})
        self.assertRedirects(response, self.url('portal_review', review)+'#discussion')
        self.assertNotIn('portal_return_review', browser.session)

    def test_signin_return_never_redirects_to_another_identity_review(self):
        member, review = self.submitted()
        from .access import mint_email_token
        browser = Browser()
        browser.get(self.url('portal_review', review))
        _, token = mint_email_token(self.other.client_contact.email, destination='portal')
        response = browser.post(reverse('workqueue:confirm_access'), {'token':token})
        self.assertRedirects(response, reverse('workqueue:portal_home'))

    def test_malformed_review_return_is_ignored(self):
        member, _ = self.submitted()
        from .access import mint_email_token
        browser = Browser()
        session = browser.session
        session['portal_return_project'] = str(self.project.pk)
        session['portal_return_review'] = 'https://example.invalid/'
        session.save()
        _, token = mint_email_token(member.email, destination='portal')
        response = browser.post(reverse('workqueue:confirm_access'), {'token':token})
        self.assertRedirects(response, reverse('workqueue:portal_project', args=[self.project.pk]))
