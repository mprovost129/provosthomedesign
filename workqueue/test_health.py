from datetime import timedelta
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .health import health_snapshot, worker_started, worker_finished
from .models import NotificationDelivery, QueueWorkerHealth


@override_settings(WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class QueueHealthTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_heartbeat_records_success_and_ignores_older_overlapping_run(self):
        first, second = worker_started(), worker_started()
        worker_finished(first, ['old-failure'])
        self.assertIsNone(QueueWorkerHealth.objects.get().last_finished)
        worker_finished(second, [])
        self.assertTrue(health_snapshot()['ok'])

    def test_stalled_and_failed_runs_are_visible_without_customer_data(self):
        token = worker_started()
        worker_finished(token, ['process_queue_emails'])
        self.assertFalse(health_snapshot()['ok'])
        QueueWorkerHealth.objects.filter(pk=1).update(created_at=timezone.now()-timedelta(minutes=20))
        response = self.client.get(reverse('workqueue:health'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'ok': False})
        self.assertIn('no-store', response['Cache-Control'])

    def test_old_pending_and_unknown_emails_raise_health_attention(self):
        worker_finished(worker_started(), [])
        NotificationDelivery.objects.create(recipient_kind='client', recipient='private@example.invalid',
            body='PRIVATE BODY', created_at=timezone.now()-timedelta(minutes=20))
        snapshot = health_snapshot()
        self.assertFalse(snapshot['ok'])
        self.assertNotIn('PRIVATE', str(snapshot))
        self.assertNotIn('private@example.invalid', str(snapshot))

    def test_disabled_intake_needs_no_worker(self):
        with override_settings(WORK_INTAKE_ENABLED=False):
            self.assertEqual(self.client.get(reverse('workqueue:health')).json(), {'ok': True, 'enabled': False})
