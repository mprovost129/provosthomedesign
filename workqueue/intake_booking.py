"""A short-lived booking convenience for the browser that submitted the work.

This is a receipt context, never email verification or project authorization.
"""
import uuid
from datetime import timedelta

from django.utils import timezone

from .models import Submission, WorkItem

SESSION_KEY = 'queue_booking_submission'
ANSWER_LABEL = 'Request an appointment'


def begin_booking(request, item, nonce):
    # Use the accepted submission, not changed answers on a double-click retry.
    submission = Submission.objects.get(idempotency_key=f'web:{nonce}', work_item=item)
    if submission.answers.get(ANSWER_LABEL) != 'Yes':
        request.session.pop(SESSION_KEY, None)
        return False
    request.session[SESSION_KEY] = {'item': str(item.pk),
        'expires': (timezone.now() + timedelta(hours=4)).timestamp()}
    return True


def booking_submission(request):
    context = request.session.get(SESSION_KEY)
    if not isinstance(context, dict):
        return None
    expires = context.get('expires')
    try:
        item_id = uuid.UUID(context.get('item', ''))
    except (ValueError, TypeError, AttributeError):
        request.session.pop(SESSION_KEY, None)
        return None
    if (type(expires) not in (int, float) or not timezone.now().timestamp() < expires < float('inf')
            or str(item_id) not in request.session.get('queue_recent_requests', [])):
        request.session.pop(SESSION_KEY, None)
        return None
    item = WorkItem.objects.select_related('project').filter(pk=item_id, deleted_at__isnull=True).first()
    if item is None:
        request.session.pop(SESSION_KEY, None)
    return item
