"""Privacy-limited error capture and a throttled, durable owner alert outbox."""
import hashlib
import logging
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

from django import forms
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, Http404
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from .operations_models import OperationalIncident, OperationsSettings, incident_reference

logger = logging.getLogger(__name__)


def capture_failure(area, method='JOB', exception=None):
    """Persist code location, never exception messages, locals or HTTP inputs."""
    error_type = type(exception).__name__[:150] if exception is not None else 'ServerError'
    location = ''
    if exception is not None and exception.__traceback__ is not None:
        frames = traceback.extract_tb(exception.__traceback__)
        application_frames = [f for f in frames if not any(p in f.filename.replace('\\','/')
            for p in ['/site-packages/', '/Lib/'])]
        if application_frames:
            frame = application_frames[-1]
            location = f'{Path(frame.filename).name}:{frame.lineno} · {frame.name}'[:200]
    area, method = area[:200], method[:10]
    fingerprint = hashlib.sha256(f'{area}|{method}|{error_type}|{location}'.encode()).hexdigest()
    with transaction.atomic():
        incident, _ = OperationalIncident.objects.get_or_create(fingerprint=fingerprint,
            defaults={'area':area,'method':method,'error_type':error_type,'location':location})
        OperationalIncident.objects.filter(pk=incident.pk).update(occurrences=F('occurrences')+1,last_seen=timezone.now())
    return incident.reference


def capture_request(request, exception=None):
    if getattr(request, 'phd_error_reference', None):
        return request.phd_error_reference
    # Only resolved route names are captured. Raw paths can contain email tokens
    # and customer identifiers, including on pages outside the queue.
    match = getattr(request, 'resolver_match', None)
    area = match.view_name if match else 'unmatched-request'
    reference = incident_reference()
    request.phd_error_reference = reference
    try:
        reference = capture_failure(area, request.method, exception)
        request.phd_error_reference = reference
    except Exception:
        # A broken database must not replace the original error or its response.
        logger.error('Operational error %s could not be recorded.', reference)
    return reference


class OperationalErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if 500 <= response.status_code <= 599:
            response['X-Error-Reference'] = capture_request(request)
            response['Cache-Control'] = 'max-age=0, no-cache, no-store, must-revalidate, private'
        return response

    def process_exception(self, request, exception):
        if not isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            capture_request(request, exception)
        return None


def server_error(request, template_name='500.html'):
    reference = capture_request(request)
    try:
        response = render(request, template_name, {'error_reference':reference}, status=500)
    except Exception:
        # Still readable when the database, static manifest or normal header fails.
        response = HttpResponse('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Request could not be completed · Provost Home Design</title><h1>We could not complete your request</h1>'
            '<p>Please contact Mike before submitting again if you are unsure whether your work was received.</p>'
            f'<p>Error reference: {escape(reference)}</p><a href="/">Return to the website</a></html>',status=500)
    response['X-Error-Reference'] = reference
    response['Cache-Control'] = 'max-age=0, no-cache, no-store, must-revalidate, private'
    return response


def pending_incidents():
    return OperationalIncident.objects.filter(occurrences__gt=F('reported_count')).filter(
        occurrences__gt=F('reviewed_count'))


def owner_email():
    try:
        return forms.EmailField().clean(getattr(settings,'INTAKE_OWNER_EMAIL',''))
    except forms.ValidationError:
        return ''


def create_notice(subject, body, test=False):
    from .models import NotificationDelivery
    return NotificationDelivery.objects.create(recipient_kind='owner', recipient=owner_email(),
        subject=subject,body=body,provider_reference=('operationstest-' if test else 'operationserror-')+uuid.uuid4().hex)


@transaction.atomic
def queue_error_alerts():
    if not getattr(settings,'WORK_INTAKE_ENABLED',False) or not owner_email() or not pending_incidents().exists():
        return 0
    OperationsSettings.objects.get_or_create(pk=1)
    state=OperationsSettings.objects.select_for_update().get(pk=1)
    now=timezone.now()
    if not state.email_alerts or state.last_queued_at and state.last_queued_at > now-timedelta(hours=1):
        return 0
    # Queue oldest unreported incidents first so a repeated hot error cannot
    # starve other alerts. Lock only incidents actually included in this email.
    incidents=list(pending_incidents().select_for_update().order_by('last_seen','pk')[:20])
    if not incidents:
        return 0
    base=getattr(settings,'INTAKE_PUBLIC_BASE_URL','').rstrip('/')
    body='Provost Home Design recorded unexpected errors. Review them in your staff workspace:\n'+base+reverse('workqueue:operations')+'\n\n'
    for incident in incidents:
        body+=(f'{incident.reference}\nArea: {incident.area}\nType: {incident.error_type}\n'
               f'Occurrences since last alert: {incident.occurrences-incident.reported_count}\n'
               f'Last recorded: {timezone.localtime(incident.last_seen):%b %d, %Y %I:%M %p %Z}\n\n')
        incident.reported_count=incident.occurrences
        incident.save(update_fields=['reported_count'])
    body+='Alerts are grouped at most once per hour. Opening the list does not resend messages.\nClient answers, documents, passwords and sign-in links are not included.'
    notice=create_notice(f'[PHD] Website errors need attention ({len(incidents)})',body)
    state.last_queued_at,state.last_delivery=now,notice
    state.save(update_fields=['last_queued_at','last_delivery'])
    return len(incidents)


@transaction.atomic
def queue_test_alert():
    if not owner_email():
        raise forms.ValidationError('The owner email address is not configured.')
    OperationsSettings.objects.get_or_create(pk=1)
    state=OperationsSettings.objects.select_for_update().get(pk=1)
    now=timezone.now()
    if state.last_test_at and state.last_test_at > now-timedelta(minutes=5):
        raise forms.ValidationError('A test was queued recently. Please wait five minutes before another test.')
    notice=create_notice('[TEST] Provost Home Design error alerts',
        'This is a test of your website error alerts. No real error was triggered and no client submission was changed.\n\n'
        'Unexpected errors will be grouped into an owner email at most once per hour. Your independent outage monitor remains in place.\n\n'
        +getattr(settings,'INTAKE_PUBLIC_BASE_URL','').rstrip('/')+reverse('workqueue:operations'),test=True)
    state.last_test_at,state.last_test_delivery=now,notice
    state.save(update_fields=['last_test_at','last_test_delivery'])
    return notice
