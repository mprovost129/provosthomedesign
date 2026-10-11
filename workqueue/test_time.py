import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import close_old_connections, IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, Client, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, NotificationDelivery, QueueState
from .time_models import TimeEntry
from .time_services import start_timer, stop_timer, save_time, archive_time, time_summary, active_timer
from .services import create_work, EditConflict
from .tests import work_data
from .pm_services import save_plan


def fields(item, **changes):
    data = dict(token=uuid.uuid4(), version=0, project=item.project, work_item=item,
        category='design', description='Measured actual work', billable=True,
        minutes=Decimal('90'), work_date=timezone.localdate())
    data.update(changes)
    return data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_OWNER_EMAIL='owner@example.invalid')
class TimeTrackingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('time-owner', 'owner@example.invalid', 'test-only')
        self.other = get_user_model().objects.create_superuser('time-other', 'other@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.project = self.item.project
        self.url = reverse('workqueue:project_time', args=[self.project.pk])
        self.timer_url = reverse('workqueue:timer')
        self.client.force_login(self.owner)

    def post_timer(self, action, **data):
        return self.client.post(self.timer_url, {'action':action, **data}, HTTP_ACCEPT='application/json')

    def test_elapsed_server_time_and_duplicate_stop(self):
        now = timezone.now()
        with patch('workqueue.time_services.timezone.now', return_value=now):
            timer = start_timer(self.project.pk, fields(self.item), self.owner)
        with patch('workqueue.time_services.timezone.now', return_value=now+timedelta(minutes=17, seconds=3)):
            first = stop_timer(timer.pk, self.owner)
            second = stop_timer(timer.pk, self.owner)
        self.assertEqual(first.duration_seconds, 1023)
        self.assertEqual(first.version, second.version)
        self.assertEqual(TimeEntry.objects.count(), 1)
        self.assertIsNone(active_timer(self.owner))

    def test_duplicate_start_returns_same_record_without_restarting(self):
        data = fields(self.item)
        timer = start_timer(self.project.pk, data, self.owner)
        self.assertEqual(start_timer(self.project.pk, data, self.owner).started_at, timer.started_at)
        with self.assertRaises(EditConflict):
            start_timer(self.project.pk, fields(self.item), self.owner)
        stop_timer(timer.pk, self.owner)
        new = start_timer(self.project.pk, fields(self.item), self.owner)
        stop_timer(timer.pk, self.owner)  # A stale tab cannot stop the new timer.
        self.assertEqual(active_timer(self.owner).pk, new.pk)

    def test_timers_are_per_staff_member_and_cannot_stop_another_users_timer(self):
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        start_timer(self.project.pk, fields(self.item), self.other)
        with self.assertRaises(TimeEntry.DoesNotExist):
            stop_timer(timer.pk, self.other)
        self.assertEqual(TimeEntry.objects.count(), 2)

    def test_discard_is_confirmed_and_excluded_from_hours(self):
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        self.assertEqual(self.post_timer('discard', entry_id=timer.pk).status_code, 400)
        self.assertTrue(active_timer(self.owner))
        self.assertEqual(self.post_timer('discard', entry_id=timer.pk, confirm_discard='yes').status_code, 200)
        self.assertEqual(time_summary(self.project)['total'], 0)
        self.assertIsNone(TimeEntry.objects.get(pk=timer.pk).duration_seconds)

    def test_manual_double_submit_once_and_correction_conflict(self):
        data = fields(self.item)
        entry = save_time(self.project.pk, data, self.owner)
        self.assertEqual(save_time(self.project.pk, data, self.owner).pk, entry.pk)
        correction = fields(self.item, token=entry.pk, version=entry.version, minutes=45, billable=False)
        updated = save_time(self.project.pk, correction, self.owner)
        with self.assertRaises(EditConflict):
            save_time(self.project.pk, correction, self.other)
        self.assertEqual(updated.duration_seconds, 2700)
        self.assertFalse(updated.billable)
        event = self.project.planning_events.get(action='Time entry corrected')
        self.assertEqual(event.details['before']['seconds'], 5400)
        self.assertEqual(event.details['seconds'], 2700)

    def test_timer_correction_keeps_original_clock_and_source(self):
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        stopped = stop_timer(timer.pk, self.owner)
        corrected = save_time(self.project.pk, fields(self.item, token=timer.pk,
            version=stopped.version, minutes=30), self.owner)
        self.assertEqual(corrected.started_at, stopped.started_at)
        self.assertEqual(corrected.ended_at, stopped.ended_at)
        self.assertEqual(corrected.source, 'timer')
        self.assertEqual(corrected.duration_seconds, 1800)

    def test_remove_restore_and_stale_removal_are_safe(self):
        entry = save_time(self.project.pk, fields(self.item), self.owner)
        removed = archive_time(self.project.pk, entry.pk, entry.version, self.owner)
        self.assertEqual(time_summary(self.project)['total'], 0)
        with self.assertRaises(EditConflict):
            archive_time(self.project.pk, entry.pk, entry.version, self.owner)
        self.assertContains(self.client.get(self.url+'?show=removed'), entry.description)
        archive_time(self.project.pk, entry.pk, removed.version, self.owner, restore=True)
        self.assertEqual(time_summary(self.project)['total'], Decimal('1.50'))

    def test_project_mismatch_rejected(self):
        other = create_work(data=work_data(project_name='Separate project', project_street='900 Other St'), actor=self.owner)[0]
        with self.assertRaises(ValidationError):
            start_timer(self.project.pk, fields(other), self.owner)
        entry = save_time(self.project.pk, fields(self.item), self.owner)
        with self.assertRaises(ValidationError):
            save_time(other.project_id, fields(other, token=entry.pk, version=entry.version), self.owner)

    def test_future_date_invalid_duration_and_active_correction_rejected(self):
        for changes in [dict(minutes=0), dict(minutes=1441), dict(minutes='NaN'),
                        dict(minutes='bad'), dict(work_date=timezone.localdate()+timedelta(days=1))]:
            with self.assertRaises(ValidationError):
                save_time(self.project.pk, fields(self.item, **changes), self.owner)
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        with self.assertRaises(ValidationError):
            save_time(self.project.pk, fields(self.item, token=timer.pk, version=timer.version), self.owner)

    def test_summary_split_estimate_and_no_queue_or_billing_side_effects(self):
        before = list(WorkItem.objects.values_list('pk', 'status', 'version', 'queue_order'))
        deliveries = NotificationDelivery.objects.count()
        save_plan(self.project.pk, 0, dict(scope='', internal_notes='', target_date=None, estimated_hours=Decimal('2')), self.owner)
        save_time(self.project.pk, fields(self.item), self.owner)
        save_time(self.project.pk, fields(self.item, minutes=60, billable=False), self.owner)
        start_timer(self.project.pk, fields(self.item), self.owner)
        totals = time_summary(self.project)
        self.assertEqual((totals['total'], totals['billable'], totals['nonbillable']), (Decimal('2.50'), Decimal('1.50'), Decimal('1.00')))
        self.assertTrue(totals['over_budget'])
        self.assertEqual(totals['remaining'], Decimal('-.50'))
        self.assertEqual(before, list(WorkItem.objects.values_list('pk', 'status', 'version', 'queue_order')))
        self.assertEqual(deliveries, NotificationDelivery.objects.count())
        self.assertFalse(self.project.proposals.exists())

    def test_get_state_persists_across_clients_and_marks_long_timer(self):
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        TimeEntry.objects.filter(pk=timer.pk).update(started_at=timezone.now()-timedelta(hours=13))
        browser = Client(); browser.force_login(self.owner)
        response = browser.get(self.timer_url)
        self.assertTrue(response.json()['needs_review'])
        self.assertEqual(response.json()['id'], str(timer.pk))
        self.assertIn('no-cache', response['Cache-Control'])

    def test_staff_auth_permissions_and_readonly(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.timer_url).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        user = get_user_model().objects.create_user('read-only', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertNotContains(self.client.get(self.url), 'Start tracking time')
        self.assertEqual(self.post_timer('start').status_code, 403)
        with self.assertRaises(PermissionDenied):
            save_time(self.project.pk, fields(self.item), user)

    def test_real_https_csrf_required_and_succeeds_for_timer(self):
        browser = Client(enforce_csrf_checks=True); browser.force_login(self.owner)
        page = browser.get(self.url, secure=True)
        self.assertEqual(page.status_code, 200)
        token = browser.cookies['csrftoken'].value
        data = dict(action='start', token=str(uuid.uuid4()), project=str(self.project.pk), category='design', billable='on')
        self.assertEqual(browser.post(self.timer_url, data, secure=True, HTTP_ORIGIN='https://testserver').status_code, 403)
        response = browser.post(self.timer_url, data, secure=True, HTTP_ORIGIN='https://testserver',
            HTTP_X_CSRFTOKEN=token, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['running'])

    def test_invalid_manual_post_preserves_fields_and_has_no_write(self):
        response = self.client.post(self.url, dict(action='save', token=uuid.uuid4(), version=0,
            project=self.project.pk, category='design', minutes=-2, work_date=timezone.localdate(), description='Keep this draft'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Keep this draft', status_code=400)
        self.assertEqual(TimeEntry.objects.count(), 0)

    def test_timer_response_updates_selected_project_totals(self):
        timer = start_timer(self.project.pk, fields(self.item), self.owner)
        TimeEntry.objects.filter(pk=timer.pk).update(started_at=timezone.now()-timedelta(minutes=30))
        response = self.client.post(self.timer_url+'?project='+str(self.project.pk),
            dict(action='stop', entry_id=timer.pk), HTTP_ACCEPT='application/json')
        self.assertEqual(response.json()['totals']['total'], '0.50')
        self.assertIn('updated entry list', response.json()['message'])

    def test_queue_workspace_and_time_pages_render_timer_without_duplicate_ids(self):
        for url in [reverse('workqueue:queue'), reverse('workqueue:project_detail', args=[self.project.pk]), self.url]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Your work timer')
            self.assertContains(response, 'id="timer_category"', count=1)
            self.assertContains(response, 'id="timer_work_item"', count=1)

    def test_database_constraints_reject_invalid_manual_and_second_running_timer(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(project=self.project, actor=self.owner, source='manual', duration_seconds=None)
        start_timer(self.project.pk, fields(self.item), self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(project=self.project, actor=self.owner, source='timer', started_at=timezone.now())


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_OWNER_EMAIL='owner@example.invalid')
class TimeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_superuser('time-race-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]

    def concurrent(self, operation):
        def isolated(_):
            close_old_connections()
            try:
                return operation()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(isolated, range(2)))

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_start_allows_only_one_active_timer(self):
        def start():
            try:
                start_timer(self.item.project_id, fields(self.item), self.owner)
                return 'started'
            except EditConflict:
                return 'conflict'
        self.assertCountEqual(self.concurrent(start), ['started', 'conflict'])
        self.assertEqual(TimeEntry.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_stop_records_once(self):
        entry = start_timer(self.item.project_id, fields(self.item), self.owner)
        results = self.concurrent(lambda: stop_timer(entry.pk, self.owner).version)
        self.assertEqual(results, [2, 2])
        self.assertEqual(self.item.project.planning_events.filter(action='Time recorded').count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_manual_duplicate_requests_create_once(self):
        data = fields(self.item)
        result = self.concurrent(lambda: save_time(self.item.project_id, data, self.owner).pk)
        self.assertEqual(result[0], result[1])
        self.assertEqual(TimeEntry.objects.count(), 1)
