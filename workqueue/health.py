"""A database heartbeat exposes no customer data or provider credentials."""
import uuid
from datetime import timedelta
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import Appointment, NotificationDelivery, QueueWorkerHealth


def worker_started():
    token = uuid.uuid4()
    QueueWorkerHealth.objects.get_or_create(pk=1)
    QueueWorkerHealth.objects.filter(pk=1).update(run_token=token, last_started=timezone.now())
    return token


def worker_finished(token, failed):
    fields = {'last_finished': timezone.now(), 'failed_tasks': failed}
    if not failed:
        fields['last_success'] = fields['last_finished']
    QueueWorkerHealth.objects.filter(pk=1, run_token=token).update(**fields)


def health_snapshot():
    now = timezone.now()
    record = QueueWorkerHealth.objects.filter(pk=1).first()
    issues = []
    if record is None or (record.last_success or record.created_at) < now-timedelta(minutes=15):
        issues.append('Background jobs have not completed successfully within 15 minutes.')
    if record and record.failed_tasks:
        issues.append('The last background run reported a task failure.')
    if NotificationDelivery.objects.filter(state='pending', created_at__lt=now-timedelta(minutes=15)).exists():
        issues.append('An email has been waiting to send for more than 15 minutes.')
    if NotificationDelivery.objects.filter(state__in=['failed', 'unknown']).exists():
        issues.append('Email delivery needs attention. Check Email issues before retrying.')
    if getattr(settings, 'BOOKING_ENABLED', False) and Appointment.objects.filter(state='attention').exists():
        issues.append('An appointment needs a calendar connection check.')
    return {'ok': not issues, 'issues': issues, 'last_success': record.last_success if record else None}


@never_cache
@require_http_methods(['GET'])
def public_health(request):
    if not getattr(settings, 'WORK_INTAKE_ENABLED', False):
        return JsonResponse({'ok': True, 'enabled': False})
    # The independent monitor receives only an up/down signal. Never return
    # customer counts, recipients, errors from providers, tokens or filenames.
    healthy = cache.get('queue-public-health-v1')
    if healthy is None:
        try:
            healthy = health_snapshot()['ok']
        except Exception:
            healthy = False
        cache.set('queue-public-health-v1', healthy, timeout=15)
    return JsonResponse({'ok': healthy}, status=200 if healthy else 503)
