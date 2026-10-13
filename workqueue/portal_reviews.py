"""Locked autosaves and explicit, retry-proof revision submission. No mail on reads."""
import math
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .access import normalize_email
from .models import AuditEvent, NotificationDelivery, Submission, WorkItem
from .portal_models import PortalMember, PortalDocumentVersion
from .portal_review_models import PlanReview
from .services import EditConflict, create_work, lock_queue

MAX_PINS = 100
MAX_PAGES = 500


def check_member(member, revision, *, editing=True):
    member = PortalMember.objects.select_related('client', 'contact', 'project').get(pk=member.pk)
    revision = PortalDocumentVersion.objects.select_related('document').get(pk=revision.pk)
    if (member.revoked_at or revision.document.project_id != member.project_id or revision.document.withdrawn_at
            or revision.document.kind != 'plan' or revision.external_url or not revision.storage_key
            or revision.content_type != 'application/pdf'):
        raise PermissionDenied
    if editing and (not member.review_access or member.client.merged_into_id or member.contact.archived_at
            or member.contact.client_id != member.client_id or normalize_email(member.contact.email or '') != member.email):
        raise PermissionDenied('Your plan review access needs to be approved again. Contact Provost Home Design.')
    return member, revision


def check_identity(review, member, *, editing=True):
    if (review.member_id != member.pk or review.author_email != member.email or review.client_id != member.client_id
            or review.contact_id != member.contact_id or (editing and review.grant_at != member.granted_at)):
        raise PermissionDenied


@transaction.atomic
def start_review(member, revision, token):
    lock_queue()
    member, revision = check_member(member, revision)
    token = uuid.UUID(str(token))
    existing = PlanReview.objects.filter(pk=token).first()
    if existing:
        check_identity(existing, member)
        if existing.document_version_id != revision.pk:
            raise PermissionDenied
        return existing
    draft = PlanReview.objects.filter(member=member, document_version=revision,
        grant_at=member.granted_at, submitted_at__isnull=True).first()
    if draft:
        check_identity(draft, member)
        return draft
    return PlanReview.objects.create(id=token, member=member, document_version=revision, client=member.client,
        contact=member.contact, author_email=member.email, author_name=member.contact.full_name or member.email,
        company_name=member.client.name, grant_at=member.granted_at, document_title=revision.document.title)


def clean_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {'version', 'summary', 'annotations', 'geometry'}:
        raise ValidationError('Invalid plan review.')
    version, summary, pins, pages = (payload[key] for key in ['version', 'summary', 'annotations', 'geometry'])
    if type(version) is not int or version < 1 or not isinstance(summary, str) or len(summary) > 6000:
        raise ValidationError('Review notes must be at most 6,000 characters.')
    if not isinstance(pins, list) or len(pins) > MAX_PINS or not isinstance(pages, list) or len(pages) > MAX_PAGES:
        raise ValidationError('Use at most 100 notes on a plan with up to 500 pages.')
    def number(value, low, high):
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    seen_pages, seen_pins = set(), set()
    for page in pages:
        if (not isinstance(page, dict) or set(page) != {'page', 'width', 'height', 'rotation'}
                or type(page['page']) is not int or not 1 <= page['page'] <= MAX_PAGES
                or page['page'] in seen_pages or not number(page['width'], 1, 20000)
                or not number(page['height'], 1, 20000) or type(page['rotation']) is not int
                or page['rotation'] not in {0, 90, 180, 270}):
            raise ValidationError('Invalid plan page geometry.')
        seen_pages.add(page['page'])
    for pin in pins:
        if (not isinstance(pin, dict) or set(pin) != {'id', 'page', 'x', 'y', 'text'}
                or not isinstance(pin['id'], str) or len(pin['id']) > 36 or pin['id'] in seen_pins
                or type(pin['page']) is not int or pin['page'] not in seen_pages
                or not number(pin['x'], 0, 1) or not number(pin['y'], 0, 1)
                or not isinstance(pin['text'], str) or len(pin['text']) > 1500
                or any(ord(char) < 32 and char not in '\n\r\t' for char in pin['text'])):
            raise ValidationError('Each note needs a valid page, location, and at most 1,500 characters.')
        try:
            uuid.UUID(pin['id'])
        except ValueError:
            raise ValidationError('Invalid note reference.') from None
        seen_pins.add(pin['id'])
    if any(ord(char) < 32 and char not in '\n\r\t' for char in summary):
        raise ValidationError('Invalid characters in the review notes.')
    return version, summary.strip(), pins, pages


def update_locked(review, member, payload):
    member, _ = check_member(member, review.document_version)
    check_identity(review, member)
    version, summary, pins, pages = clean_payload(payload)
    if review.submitted_at:
        raise EditConflict('This review was submitted. Start a new review to request further changes.')
    if review.version != version:
        raise EditConflict('This draft changed in another tab. Your edits are still here; copy them before reloading.')
    review.summary, review.annotations, review.geometry = summary, pins, pages
    review.version += 1
    review.save(update_fields=['summary', 'annotations', 'geometry', 'version', 'updated_at'])
    return review, member


@transaction.atomic
def save_review(review_id, member, payload):
    lock_queue()
    review = PlanReview.objects.select_for_update(of=('self',)).select_related('document_version__document').get(pk=review_id)
    return update_locked(review, member, payload)[0]


def review_text(review):
    revision = review.document_version
    parts = [f'{review.document_title} — Version {revision.sequence}', f'File: {revision.original_name}', review.summary]
    for index, pin in enumerate(review.annotations, 1):
        parts.append(f'Note {index} · Page {pin["page"]} · {pin["x"]:.1%} from left, {pin["y"]:.1%} from top\n{pin["text"]}')
    return '\n\n'.join(part for part in parts if part)


@transaction.atomic
def submit_review(review_id, member, payload):
    lock_queue()
    review = PlanReview.objects.select_for_update(of=('self',)).select_related('document_version__document', 'work_item').get(pk=review_id)
    member, _ = check_member(member, review.document_version)
    check_identity(review, member)
    # A lost response or a double-click returns the original committed request.
    if review.submitted_at:
        return review, False
    review, member = update_locked(review, member, payload)
    if not review.summary and not any(pin['text'].strip() for pin in review.annotations):
        raise ValidationError('Describe at least one requested change before submitting.')
    if any(not pin['text'].strip() for pin in review.annotations):
        raise ValidationError('Add a comment to each pin, or remove its empty note.')
    project = member.project
    contact, client = member.contact, member.client
    data = dict(kind='update', project=project, project_context=project.canonical_reference or project.name,
        contact_full_name=review.author_name, company=review.company_name, contact_email=member.email,
        contact_phone=contact.phone, billing_street=client.billing_street, billing_city=client.billing_city,
        billing_state=client.billing_state, billing_zip=client.billing_zip, project_name=project.name,
        project_street=project.street, project_city=project.city, project_state=project.state,
        project_zip=project.zip_code, service_needed='Plan revisions', description=review_text(review), source='Client portal')
    item, _ = create_work(data=data, actor=None, idempotency_key=f'portal-review:{review.pk}', portal_member=member)
    submission = Submission.objects.get(work_item=item, idempotency_key=f'portal-review:{review.pk}')
    submission.channel = 'portal'
    submission.answers = {'Project': project.name, 'Plan': review.document_title,
        'Version': str(review.document_version.sequence), 'File': review.document_version.original_name,
        'Requested changes': review_text(review)}
    submission.save(update_fields=['channel', 'answers'])
    review.work_item, review.submitted_at = item, timezone.now()
    review.save(update_fields=['work_item', 'submitted_at', 'updated_at'])
    AuditEvent.objects.create(work_item=item, action='plan_review_submitted',
        changes={'review': str(review.pk), 'document_version': str(review.document_version_id), 'email': member.email})
    from .pm_services import event
    event(project, None, 'Client plan review submitted', reference=item.reference, review=str(review.pk))
    from .notifications import queue_receipts, site_url
    queue_receipts(submission)
    client_url = site_url(reverse('workqueue:portal_review', args=[project.pk, review.pk]))
    owner_url = site_url(reverse('workqueue:staff_plan_review', args=[project.pk, review.pk]))
    for delivery in NotificationDelivery.objects.filter(submission=submission, state='pending'):
        # Preserve the complete submitted version and notes in both receipt copies.
        delivery.body += '\n\nPLAN REVIEW\n' + review_text(review) + '\n\nOpen review:\n' + (
            client_url if delivery.recipient_kind == 'client' else owner_url) + '\n'
        delivery.save(update_fields=['body'])
    return review, True


@transaction.atomic
def mark_reviewed(review_id, version, actor):
    from .crm import require_staff
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    review = PlanReview.objects.select_for_update().get(pk=review_id, submitted_at__isnull=False)
    if review.version != version:
        raise EditConflict('This review changed. Reload it before saving.')
    if not review.reviewed_at:
        review.reviewed_at, review.reviewed_by = timezone.now(), actor
        review.version += 1
        review.save(update_fields=['reviewed_at', 'reviewed_by', 'version', 'updated_at'])
    return review
