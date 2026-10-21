from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import close_old_connections
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import path, reverse
from django.utils import timezone
from .operations import (capture_failure, capture_request, queue_error_alerts, queue_test_alert,
                         server_error, OperationalErrorMiddleware)
from .operations_models import OperationalIncident, OperationsSettings
from .models import NotificationDelivery
from .notifications import deliver_pending


def broken_view(request):
    raise ValueError('PRIVATE_PASSWORD client@example.invalid /upload/client-plan.pdf token=SECRET')


def not_found(request):
    raise Http404('PRIVATE')


def forbidden(request):
    raise PermissionDenied('PRIVATE')


def bad_gateway(request):
    return HttpResponse('Upstream unavailable',status=502)


urlpatterns=[path('_test-error/',broken_view,name='test_error'),path('_test-missing/',not_found),
             path('_test-forbidden/',forbidden),path('_test-gateway/',bad_gateway)]
from config.urls import urlpatterns as real_urls
urlpatterns+=real_urls
handler500=server_error


@override_settings(WORK_INTAKE_ENABLED=True, INTAKE_OWNER_EMAIL='owner@example.invalid',
                   INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com',ROOT_URLCONF=__name__)
class OperationsTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('ops-owner','','test')
        self.url=reverse('workqueue:operations')

    def incident(self, area='workqueue:submit'):
        capture_failure(area,exception=ValueError('PRIVATE DATA'))
        return OperationalIncident.objects.get(area=area)

    @override_settings(DEBUG=False)
    def test_unexpected_post_error_has_reference_and_only_safe_metadata(self):
        browser=Client(raise_request_exception=False)
        response=browser.post('/_test-error/?token=PRIVATE_QUERY',{'password':'PRIVATE_BODY','email':'private@example.invalid'})
        self.assertEqual(response.status_code,500)
        incident=OperationalIncident.objects.get()
        self.assertEqual(incident.occurrences,1)
        self.assertEqual(incident.area,'test_error')
        self.assertEqual(incident.method,'POST')
        self.assertEqual(incident.error_type,'ValueError')
        self.assertEqual(response['X-Error-Reference'],incident.reference)
        self.assertContains(response,incident.reference,status_code=500)
        self.assertIn('no-store',response['Cache-Control'])
        serialized=str(incident.__dict__)+response.content.decode()
        for secret in ['PRIVATE_PASSWORD','client@example.invalid','PRIVATE_QUERY','PRIVATE_BODY','token=SECRET']:
            self.assertNotIn(secret,serialized)

    @override_settings(DEBUG=False)
    def test_expected_http_errors_do_not_create_incidents(self):
        browser=Client(raise_request_exception=False)
        for path,status in [('/_test-missing/',404),('/_test-forbidden/',403),('/does-not-exist/',404)]:
            self.assertEqual(browser.get(path).status_code,status)
        self.assertEqual(OperationalIncident.objects.count(),0)

    def test_explicit_server_response_is_recorded_without_body_capture(self):
        response=self.client.get('/_test-gateway/')
        self.assertEqual(response.status_code,502)
        self.assertEqual(OperationalIncident.objects.get().occurrences,1)
        self.assertIn('X-Error-Reference',response)

    def test_repeated_failures_group_and_separate_areas_do_not(self):
        first=capture_failure('workqueue:submit',exception=ValueError('one'))
        second=capture_failure('workqueue:submit',exception=ValueError('different private answer'))
        self.assertEqual(first,second)
        self.assertEqual(OperationalIncident.objects.get().occurrences,2)
        capture_failure('workqueue:queue',exception=ValueError('one'))
        self.assertEqual(OperationalIncident.objects.count(),2)

    def test_capture_is_idempotent_per_request_and_failures_are_nonfatal(self):
        request=RequestFactory().get('/secret-token-location/?email=PRIVATE')
        self.assertEqual(capture_request(request),capture_request(request))
        self.assertEqual(OperationalIncident.objects.get().occurrences,1)
        self.assertEqual(OperationalIncident.objects.get().area,'unmatched-request')
        with patch('workqueue.operations.capture_failure',side_effect=RuntimeError('PRIVATE_DB_URL')):
            failed=RequestFactory().get('/private')
            reference=capture_request(failed)
        self.assertTrue(reference.startswith('ERR-'))
        self.assertNotIn('PRIVATE',reference)

    def test_error_page_falls_back_without_normal_template(self):
        with patch('workqueue.operations.render',side_effect=RuntimeError('PRIVATE_DB_URL')):
            response=server_error(RequestFactory().post('/secret'))
        self.assertEqual(response.status_code,500)
        self.assertContains(response,'Error reference:',status_code=500)
        self.assertNotIn('PRIVATE_DB_URL',response.content.decode())

    def test_alert_is_atomic_grouped_and_hourly_not_per_request(self):
        one=self.incident()
        self.incident('workqueue:queue')
        self.assertEqual(queue_error_alerts(),2)
        self.assertEqual(queue_error_alerts(),0)
        notice=NotificationDelivery.objects.get()
        self.assertEqual(notice.recipient,'owner@example.invalid')
        self.assertIn(one.reference,notice.body)
        self.assertIn('/dashboard/errors/',notice.body)
        self.assertNotIn('PRIVATE DATA',notice.body)
        capture_failure('workqueue:submit',exception=ValueError('another'))
        self.assertEqual(queue_error_alerts(),0)
        OperationsSettings.objects.update(last_queued_at=timezone.now()-timedelta(hours=2))
        self.assertEqual(queue_error_alerts(),1)
        self.assertEqual(NotificationDelivery.objects.count(),2)

    def test_failed_notice_creation_does_not_consume_unreported_counts(self):
        incident=self.incident()
        with patch('workqueue.operations.create_notice',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):queue_error_alerts()
        incident.refresh_from_db()
        self.assertEqual(incident.reported_count,0)
        self.assertEqual(queue_error_alerts(),1)

    def test_batch_is_bounded_and_remaining_issues_can_follow(self):
        for index in range(23):self.incident('area-'+str(index))
        self.assertEqual(queue_error_alerts(),20)
        self.assertEqual(OperationalIncident.objects.filter(reported_count=0).count(),3)
        OperationsSettings.objects.update(last_queued_at=timezone.now()-timedelta(hours=2))
        self.assertEqual(queue_error_alerts(),3)

    @override_settings(INTAKE_OWNER_EMAIL='')
    def test_missing_owner_does_not_queue_or_mark_reported(self):
        incident=self.incident()
        self.assertEqual(queue_error_alerts(),0)
        self.assertFalse(NotificationDelivery.objects.exists())
        incident.refresh_from_db();self.assertEqual(incident.reported_count,0)

    def test_disabled_alerts_do_not_queue_and_cancel_unsent_error_alerts(self):
        self.incident();queue_error_alerts()
        OperationsSettings.objects.update(email_alerts=False)
        capture_failure('new-area')
        self.assertEqual(queue_error_alerts(),0)
        with patch('workqueue.notifications.EmailMultiAlternatives.send') as send:
            deliver_pending()
        send.assert_not_called()
        self.assertEqual(NotificationDelivery.objects.get().state,'canceled')

    def test_delivery_uses_existing_backend_and_is_not_repeated(self):
        self.incident()
        self.assertEqual(deliver_pending(),1)
        self.assertEqual(len(mail.outbox),1)
        self.assertEqual(mail.outbox[0].to,['owner@example.invalid'])
        notice=NotificationDelivery.objects.get()
        self.assertEqual(notice.state,'sent')
        self.assertEqual(notice.body,'')
        self.assertEqual(deliver_pending(),0)

    def test_unknown_delivery_is_held_without_duplicate_send(self):
        self.incident()
        with patch('workqueue.notifications.EmailMultiAlternatives.send',side_effect=TimeoutError):
            deliver_pending()
        self.assertEqual(NotificationDelivery.objects.get().state,'unknown')
        with patch('workqueue.notifications.EmailMultiAlternatives.send') as send:
            self.assertEqual(deliver_pending(),0)
        send.assert_not_called()

    def test_safe_test_alert_is_throttled_and_does_not_fake_errors(self):
        notice=queue_test_alert()
        self.assertTrue(notice.subject.startswith('[TEST]'))
        self.assertFalse(OperationalIncident.objects.exists())
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):queue_test_alert()

    def test_permissions_and_csrf_protect_mutations(self):
        self.assertIn('/staff/login/',self.client.get(self.url).url)
        user=get_user_model().objects.create_user('viewer',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,{'action':'test'}).status_code,403)
        browser=Client(enforce_csrf_checks=True);browser.force_login(self.owner)
        self.assertEqual(browser.post(self.url,{'action':'test'}).status_code,403)

    def test_review_preserves_incident_and_new_occurrences_return_to_attention(self):
        incident=self.incident();self.client.force_login(self.owner)
        self.client.post(self.url,{'action':'review','incident':str(incident.pk),'occurrences':1})
        self.assertEqual(self.client.get(self.url).context['unreviewed_count'],0)
        self.assertEqual(queue_error_alerts(),0)
        capture_failure(incident.area,exception=ValueError('next'))
        self.assertEqual(self.client.get(self.url).context['unreviewed_count'],1)
        self.assertEqual(queue_error_alerts(),1)

    def test_stale_review_does_not_hide_new_occurrences(self):
        incident=self.incident();capture_failure(incident.area,exception=ValueError())
        self.client.force_login(self.owner)
        self.client.post(self.url,{'action':'review','incident':str(incident.pk),'occurrences':1})
        incident.refresh_from_db();self.assertEqual(incident.reviewed_count,0)

    def test_preferences_use_version_and_keep_other_monitor_unchanged(self):
        self.client.force_login(self.owner)
        self.client.post(self.url,{'action':'preferences','version':1})
        self.assertFalse(OperationsSettings.objects.get().email_alerts)
        self.client.post(self.url,{'action':'preferences','version':1,'email_alerts':'on'})
        self.assertFalse(OperationsSettings.objects.get().email_alerts)

    def test_dashboard_and_reference_search_are_private_and_get_read_only(self):
        incident=self.incident();self.client.force_login(self.owner)
        response=self.client.get(reverse('workqueue:dashboard'))
        self.assertContains(response,'1 website issue awaiting review')
        response=self.client.get(self.url,{'q':incident.reference})
        self.assertContains(response,incident.reference)
        incident.refresh_from_db();self.assertEqual(incident.reviewed_count,0)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertIn('no-store',response['Cache-Control'])


class OperationsConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_parallel_capture_keeps_one_incident_and_every_occurrence(self):
        def record(_):
            close_old_connections()
            try:return capture_failure('concurrent-area')
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            references=list(pool.map(record,range(8)))
        self.assertEqual(len(set(references)),1)
        self.assertEqual(OperationalIncident.objects.get().occurrences,8)

    @skipUnlessDBFeature('has_select_for_update')
    @override_settings(WORK_INTAKE_ENABLED=True,INTAKE_OWNER_EMAIL='owner@example.invalid')
    def test_overlapping_workers_queue_one_alert(self):
        capture_failure('one-error')
        def send(_):
            close_old_connections()
            try:return queue_error_alerts()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(send,range(4)))
        self.assertEqual(sum(results),1)
        self.assertEqual(NotificationDelivery.objects.count(),1)
