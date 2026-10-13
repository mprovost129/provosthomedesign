import uuid
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .client_views import intake_enabled, limited
from .pm_views import editable, error_text, staff_view
from .portal_discussion_models import ReviewDiscussion
from .portal_discussions import add_reply, read_replies, reply_access, set_note_state, topics
from .portal_review_views import client_review, review_context, submitted_staff_review
from .portal_views import private_view
from .services import EditConflict


def discussion_context(review, *, staff=False, page=None):
    if not review.submitted_at:
        return {}
    states = {state.note: state for state in review.note_states.all()}
    rows = []
    choices = [{'key':key, 'label':label} for key, label, _ in topics(review)]
    for key, label, text in topics(review):
        if not key and not text:
            continue
        state = states.get(key)
        rows.append({'key': key, 'label': label, 'text': text,
            'addressed': state.addressed if state else False, 'version': state.version if state else 1})
    thread = Paginator(review.discussion_messages.select_related('delivery'), 50).get_page(page)
    labels = {key: label for key, label, _ in topics(review)}
    for message in thread:
        message.topic_label = labels[message.note]
    discussion = ReviewDiscussion.objects.filter(review=review).first()
    read_through = discussion.read_through if discussion else 0
    unread = review.discussion_messages.filter(from_staff=False, pk__gt=read_through).count()
    last_client = max((message.pk for message in thread if not message.from_staff), default=0)
    can_reply = not review.work_item.deleted_at
    if not staff:
        try:
            reply_access(review, review.member)
        except PermissionDenied:
            can_reply = False
    return {'discussion_topics': rows, 'discussion_choices': choices, 'discussion_page': thread, 'reply_token': uuid.uuid4(),
        'unread_replies': unread if staff else 0, 'read_through': last_client, 'can_reply': can_reply}


def handle(request, project_id, review_id, *, staff):
    if staff:
        editable(request)
        record = submitted_staff_review(project_id, review_id)
        member = None
    else:
        member, record = client_review(request, project_id, review_id)
    status, error = 400, ''
    try:
        action = request.POST.get('action', '')
        if action == 'reply':
            if not limited(request, 'portal-reply', 'staff:'+str(request.user.pk) if staff else member.email,
                    limit=60, window=3600):
                status = 429
                raise ValidationError('Too many replies. Keep your message and try again shortly.')
            add_reply(record.pk, token=request.POST.get('token', ''), note=request.POST.get('note', ''),
                body=request.POST.get('body', ''), member=member, actor=request.user if staff else None,
                notify=staff and request.POST.get('notify') == 'on')
            messages.success(request, 'Reply saved.' + (' Client notification queued.' if staff and request.POST.get('notify') == 'on' else ''))
        elif staff and action == 'note_state':
            if request.POST.get('addressed') not in {'1', '0'}:
                raise ValidationError('Choose an open or addressed status.')
            set_note_state(record.pk, note=request.POST.get('note', ''), addressed=request.POST['addressed'] == '1',
                version=int(request.POST.get('version', '0')), actor=request.user)
            messages.success(request, 'Note status updated. Queue status is managed separately.')
        elif staff and action == 'read':
            read_replies(record.pk, through=int(request.POST.get('through', '0')), actor=request.user)
            messages.success(request, 'Replies marked read.')
        else:
            raise ValidationError('Choose a review action.')
    except (ValidationError, EditConflict, ValueError) as exc:
        status = 409 if isinstance(exc, EditConflict) else status
        error = error_text(exc) if not isinstance(exc, ValueError) else 'Reload the review before saving.'
    else:
        return redirect(reverse('workqueue:staff_plan_review' if staff else 'workqueue:portal_review',
            args=[project_id, review_id])+'#discussion')
    context = review_context(record, staff=staff)
    context.update(discussion_context(record, staff=staff, page=request.GET.get('discussion_page')))
    context.update({'error': error, 'can_manage_discussion': staff,
        'posted_reply': request.POST.get('body', '')[:4000], 'posted_topic': request.POST.get('note', ''),
        'reply_token': request.POST.get('token', '') or context.get('reply_token'),
        'posted_notify': request.POST.get('notify') == 'on'})
    return render(request, 'workqueue/portal_review.html', context, status=status)


@intake_enabled
@private_view
@require_http_methods(['POST'])
def client_action(request, project_id, review_id):
    return handle(request, project_id, review_id, staff=False)


@staff_view
@private_view
@require_http_methods(['POST'])
def staff_action(request, project_id, review_id):
    return handle(request, project_id, review_id, staff=True)
