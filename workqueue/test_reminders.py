import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import InformationRequest, InformationResponse, NotificationDelivery, Status, WorkItem
from .information_requests import request_information
from .reminders import queue_due_reminders
from .notifications import deliver_pending
from .services import create_work
from .tests import work_data


@override_settings(INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ClientReminderTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('reminder-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]

    def request(self):
        request_information(item_id=self.item.pk, version=self.item.version, message='Please send your survey.',
            followup_date=None, token=uuid.uuid4(), actor=self.owner, reminder_date=timezone.localdate()+timedelta(days=1))
        note = InformationRequest.objects.latest('pk')
        note.delivery.state = 'sent'; note.delivery.save()
        InformationRequest.objects.filter(pk=note.pk).update(reminder_date=timezone.localdate())
        return note

    def test_due_reminder_is_queued_exactly_once(self):
        note = self.request()
        self.assertEqual(queue_due_reminders(), 1)
        self.assertEqual(queue_due_reminders(), 0)
        note.refresh_from_db()
        self.assertEqual(note.reminder_delivery.state, 'pending')
        self.assertIn('Please send your survey.', note.reminder_delivery.body)
        self.assertNotIn(self.item.internal_notes, note.reminder_delivery.body) if self.item.internal_notes else None

    def test_verified_reply_stops_scheduled_and_already_pending_reminders(self):
        note = self.request()
        queue_due_reminders()
        update = create_work(data=work_data(kind='update', project=self.item.project), actor=self.owner)[0]
        InformationResponse.objects.create(information_request=note, work_item=update)
        self.assertEqual(deliver_pending(), 0)
        note.refresh_from_db()
        self.assertIsNotNone(note.reminder_canceled_at)
        self.assertEqual(note.reminder_delivery.state, 'canceled')

    def test_status_change_and_new_question_stop_old_reminder(self):
        note = self.request()
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.IN_PROGRESS)
        self.assertEqual(queue_due_reminders(), 0)
        note.refresh_from_db()
        self.assertIsNotNone(note.reminder_canceled_at)

    def test_failed_original_email_does_not_trigger_a_reminder(self):
        note = self.request()
        note.delivery.state='failed'; note.delivery.save()
        self.assertEqual(queue_due_reminders(), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_failed_question_does_not_starve_another_due_reminder(self):
        blocked = self.request()
        blocked.delivery.state = 'failed'
        blocked.delivery.save()
        self.item = create_work(data=work_data(project_name='Another project'), actor=self.owner)[0]
        eligible = self.request()
        self.assertEqual(queue_due_reminders(limit=1), 1)
        eligible.refresh_from_db()
        blocked.refresh_from_db()
        self.assertIsNotNone(eligible.reminder_delivery_id)
        self.assertIsNone(blocked.reminder_delivery_id)
