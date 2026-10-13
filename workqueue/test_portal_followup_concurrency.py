import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connection, connections
from django.test import TransactionTestCase
from . import test_portal_reviews as fixture
from .models import NotificationDelivery, QueueState, WorkItem
from .portal_discussion_models import ReviewMessage, ReviewNoteState
from .portal_discussions import add_reply, set_note_state
from .portal_notices import invite, queue_review_reminders
from .portal_notice_models import PortalInvitation, PortalReviewReminder
from .portal_reviews import submit_review
from .services import EditConflict


class FollowupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        fixture.PlanReviewTests.setUp(self)
    version = fixture.PlanReviewTests.version
    publish = fixture.PlanReviewTests.publish
    sign_in = fixture.PlanReviewTests.sign_in
    grant_review = fixture.PlanReviewTests.grant_review
    draft = fixture.PlanReviewTests.draft

    def race(self, action):
        if connection.vendor != 'postgresql':
            self.skipTest('Production locking requires PostgreSQL.')
        barrier = Barrier(2)
        def run(_):
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def test_concurrent_reply_retry_creates_one_message_and_owner_notice(self):
        member, _, review = self.draft()
        review, _ = submit_review(review.pk, member, fixture.payload(review))
        token = uuid.uuid4()
        results = self.race(lambda: add_reply(review.pk, token=token, note='', body='Clarify dimensions.', member=member)[1])
        self.assertCountEqual(results, [True, False])
        self.assertEqual(ReviewMessage.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 3)
        self.assertEqual(WorkItem.objects.count(), 3)

    def test_concurrent_note_state_changes_cannot_silently_overwrite(self):
        member, _, review = self.draft()
        review, _ = submit_review(review.pk, member, fixture.payload(review))
        def update():
            try:
                set_note_state(review.pk, note=review.annotations[0]['id'], addressed=True, version=1, actor=self.owner)
                return 'saved'
            except EditConflict:
                return 'conflict'
        self.assertCountEqual(self.race(update), ['saved','conflict'])
        self.assertEqual(ReviewNoteState.objects.count(), 1)

    def test_concurrent_invitation_retry_creates_one_email_per_recipient(self):
        member, revision, _ = self.draft()
        version, token = self.version(), uuid.uuid4()
        results = self.race(lambda: invite(self.project, version=version, token=token, member_ids=[member.pk],
            revision=revision, reminders=True, actor=self.owner)[1])
        self.assertCountEqual(results, [True, False])
        self.assertEqual(PortalInvitation.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_concurrent_reminder_workers_queue_one_copy(self):
        from datetime import timedelta
        from django.utils import timezone
        member, revision, _ = self.draft()
        batch, _ = invite(self.project, version=self.version(), token=uuid.uuid4(), member_ids=[member.pk],
            revision=revision, reminders=True, actor=self.owner)
        notice = batch.invitations.get()
        NotificationDelivery.objects.filter(pk=notice.delivery_id).update(state='sent', sent_at=timezone.now()-timedelta(days=4))
        self.assertCountEqual(self.race(lambda:queue_review_reminders()), [1,0])
        self.assertEqual(PortalReviewReminder.objects.count(), 1)
