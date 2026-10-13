"""Locked, idempotent replies and independently versioned note resolution."""
import uuid
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .models import NotificationDelivery
from .crm import require_staff
from .portal_discussion_models import ReviewDiscussion, ReviewMessage, ReviewNoteState
from .portal_review_models import PlanReview
from .portal_reviews import check_identity, check_member
from .services import EditConflict, lock_queue


def topics(review):
    return [('', 'General review', review.summary)] + [
        (str(uuid.UUID(pin['id'])), f"Note {index} · Page {pin['page']}", pin['text'])
        for index, pin in enumerate(review.annotations, 1)]


def checked_note(review, value):
    try:
        note = str(uuid.UUID(str(value))) if value else ''
    except ValueError:
        raise ValidationError('Choose a note from this review.') from None
    if note not in {key for key, _, _ in topics(review)}:
        raise ValidationError('Choose a note from this review.')
    return note


def reply_access(review, member):
    member, _ = check_member(member, review.document_version)
    check_identity(review, member, editing=False)
    if not review.submitted_at or review.work_item.deleted_at:
        raise PermissionDenied
    return member


def locked_review(review_id):
    review = PlanReview.objects.select_for_update(of=('self',)).select_related(
        'member__contact', 'member__client', 'document_version__document', 'work_item').get(pk=review_id)
    if not review.submitted_at or review.work_item.deleted_at:
        raise ValidationError('Replies are available for submitted reviews with a current queue entry.')
    return review


@transaction.atomic
def add_reply(review_id, *, token, note, body, member=None, actor=None, notify=False):
    lock_queue()
    review = locked_review(review_id)
    staff = actor is not None
    if staff:
        require_staff(actor, 'workqueue.change_workitem')
        if member is not None:
            raise PermissionDenied
    else:
        if member is None:
            raise PermissionDenied
        member = reply_access(review, member)
    try:
        token = uuid.UUID(str(token))
    except ValueError:
        raise ValidationError('Reload the review before posting.') from None
    existing = ReviewMessage.objects.filter(token=token).first()
    if existing:
        if (existing.review_id != review.pk or existing.from_staff != staff
                or (staff and existing.actor_id != actor.pk) or (not staff and existing.member_id != member.pk)):
            raise PermissionDenied
        return existing, False
    note = checked_note(review, note)
    if (not isinstance(body, str) or not body.strip() or len(body) > 4000
            or any(ord(char) < 32 and char not in '\n\r\t' for char in body)):
        raise ValidationError('Write a reply of 1–4,000 characters.')
    message = ReviewMessage.objects.create(review=review, token=token, note=note, body=body.strip(),
        actor=actor, member=member, from_staff=staff,
        author_name='Provost Home Design' if staff else review.author_name)
    from .pm_services import event
    event(review.document_version.document.project, actor, 'review_reply', review=str(review.pk), message=message.pk, from_staff=staff)
    if staff and notify:
        # Sending is explicit and must still match the frozen author of this review.
        reply_access(review, review.member)
    if not staff or notify:
        from .notifications import site_url
        recipient = review.author_email if staff else settings.INTAKE_OWNER_EMAIL
        route = 'portal_review' if staff else 'staff_plan_review'
        url = site_url(reverse('workqueue:'+route, args=[review.document_version.document.project_id, review.pk]))+'#discussion'
        delivery = NotificationDelivery.objects.create(recipient_kind='client' if staff else 'owner', recipient=recipient,
            subject='New reply on your plan review' if staff else f'Client review reply: {review.work_item.reference}',
            body=f'A new reply is available on a submitted plan review.\n\nOpen the private review:\n{url}\n\n'
                'Sign in with your approved email address.\n\nProvost Home Design\n',
            provider_reference='review-reply-'+uuid.uuid4().hex)
        message.delivery = delivery
        message.save(update_fields=['delivery'])
    return message, True


@transaction.atomic
def set_note_state(review_id, *, note, addressed, version, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    review = locked_review(review_id)
    note = checked_note(review, note)
    state = ReviewNoteState.objects.filter(review=review, note=note).first()
    if type(version) is not int or version != (state.version if state else 1):
        raise EditConflict('This note changed in another tab. Reload before updating its status.')
    if state is None:
        state = ReviewNoteState(review=review, note=note)
    state.addressed, state.version, state.actor, state.changed_at = bool(addressed), version+1, actor, timezone.now()
    state.save()
    from .pm_services import event
    event(review.document_version.document.project, actor, 'review_note_status', review=str(review.pk), note=note, addressed=state.addressed)
    return state


@transaction.atomic
def read_replies(review_id, *, through, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    review = locked_review(review_id)
    if type(through) is not int or through < 0 or (through and not review.discussion_messages.filter(pk=through, from_staff=False).exists()):
        raise ValidationError('Reload before marking replies read.')
    state, _ = ReviewDiscussion.objects.get_or_create(review=review)
    if through > state.read_through:
        state.read_through = through
        state.save(update_fields=['read_through'])
    return state


def cancel_ineligible_reply(delivery):
    message = ReviewMessage.objects.filter(delivery=delivery).select_related(
        'review__member', 'review__work_item', 'review__document_version__document').first()
    if not message:
        return False
    review = message.review
    allowed = not review.work_item.deleted_at
    if message.from_staff:
        try:
            reply_access(review, review.member)
            allowed = allowed and delivery.recipient == review.author_email
        except (PermissionDenied, ValidationError):
            allowed = False
    if not allowed:
        delivery.state, delivery.body, delivery.html_body = 'canceled', '', ''
        delivery.save(update_fields=['state', 'body', 'html_body'])
    return not allowed
