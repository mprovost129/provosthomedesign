"""Invitation audiences are frozen and checked again by the mail worker."""
import uuid
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from .access import normalize_email
from .crm import require_staff
from .models import NotificationDelivery
from .pm_services import event
from .portal_models import PortalMember
from .portal_notice_models import PortalInvitationBatch, PortalInvitation, PortalReviewReminder
from .portal_services import checked_portal, visible_documents
from .services import EditConflict, lock_queue


def valid_member(member):
    return (not member.revoked_at and not member.client.merged_into_id and not member.contact.archived_at
        and member.contact.client_id == member.client_id and normalize_email(member.contact.email or '') == member.email)


def can_invite(member, revision=None):
    if not valid_member(member):
        return False
    if revision:
        latest = revision.document.revisions.first()
        return (revision.document.project_id == member.project_id and latest and latest.pk == revision.pk
            and visible_documents(member).filter(pk=revision.document_id).exists())
    return True


def reviewable(member, revision):
    return bool(revision and member.review_access and revision.document.kind == 'plan'
        and revision.storage_key and revision.content_type == 'application/pdf' and not revision.external_url)


def invitation_body(project, revision=None, message=''):
    from .notifications import site_url
    intro = (f'A document is ready in your project portal.\n\nProject: {project.name}\n'
        f'Document: {revision.document.title}\nVersion: {revision.sequence}\n' if revision
        else f'Your project portal is ready.\n\nProject: {project.name}\n')
    return intro + ('\n'+message.strip()+'\n' if message.strip() else '') + (
        '\nOpen your private project portal:\n' + site_url(reverse('workqueue:portal_project', args=[project.pk]))
        + '\n\nSign in with the approved email address that received this message. '
        'Request a sign-in email on that page if needed.\n\nProvost Home Design\n')


@transaction.atomic
def invite(project, *, version, token, member_ids, revision=None, message='', reminders=False, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    try:
        token = uuid.UUID(str(token))
        ids = {int(value) for value in member_ids}
    except (TypeError, ValueError):
        raise ValidationError('Reload the invitation before sending.') from None
    existing = PortalInvitationBatch.objects.filter(pk=token).first()
    if existing:
        if (existing.project_id != project.pk or existing.actor_id != actor.pk
                or existing.revision_id != (revision.pk if revision else None)
                or set(existing.invitations.values_list('member_id', flat=True)) != ids):
            raise PermissionDenied
        return existing, False
    checked_portal(project, version)
    members = list(PortalMember.objects.filter(pk__in=ids, project=project).select_related('client', 'contact'))
    if not ids or len(members) != len(ids) or len(ids) > 50 or any(not can_invite(member, revision) for member in members):
        raise ValidationError('Choose current approved contacts who can see this document. Reload if sharing changed.')
    if (not isinstance(message, str) or len(message) > 2000
            or any(ord(char) < 32 and char not in '\n\r\t' for char in message)):
        raise ValidationError('Use a message of at most 2,000 characters.')
    if reminders and any(not reviewable(member, revision) for member in members):
        raise ValidationError('Review reminders require a fixed PDF plan and plan review permission for every selected contact.')
    batch = PortalInvitationBatch.objects.create(id=token, project=project, revision=revision, actor=actor)
    body = invitation_body(project, revision, message)
    subject = 'Plans ready for review — Provost Home Design' if revision and revision.document.kind == 'plan' else 'Your project portal — Provost Home Design'
    for member in members:
        if revision and reviewable(member, revision):
            earlier = PortalInvitation.objects.filter(member=member, batch__revision=revision, reminders_enabled=True)
            earlier_ids = list(earlier.values_list('pk', flat=True))
            earlier.update(reminders_enabled=False, version=F('version')+1)
            NotificationDelivery.objects.filter(portal_review_reminder__invitation_id__in=earlier_ids, state='pending').update(
                state='canceled', body='', html_body='')
        delivery = NotificationDelivery.objects.create(recipient_kind='client', recipient=member.email, subject=subject,
            body=body, provider_reference='portal-invite-'+uuid.uuid4().hex)
        PortalInvitation.objects.create(batch=batch, member=member, client=member.client, contact=member.contact,
            email=member.email, grant_at=member.granted_at, delivery=delivery,
            review_requested=reviewable(member, revision), reminders_enabled=bool(reminders))
    event(project, actor, 'portal_invitation', batch=str(batch.pk), recipients=len(members), reminders=bool(reminders))
    return batch, True


def invitation_eligible(invitation, *, reminder=False):
    member = PortalMember.objects.select_related('client', 'contact').get(pk=invitation.member_id)
    if (member.project_id != invitation.batch.project_id or member.email != invitation.email
            or member.client_id != invitation.client_id or member.contact_id != invitation.contact_id
            or member.granted_at != invitation.grant_at or not can_invite(member, invitation.batch.revision)):
        return False
    if reminder:
        revision = invitation.batch.revision
        if not invitation.reminders_enabled or not invitation.review_requested or not reviewable(member, revision):
            return False
        from .portal_review_models import PlanReview
        if PlanReview.objects.filter(member=member, client=invitation.client, contact=invitation.contact,
                author_email=invitation.email, document_version=revision, submitted_at__isnull=False).exists():
            return False
    return True


def cancel_ineligible_portal_notice(delivery):
    notice = PortalInvitation.objects.filter(delivery=delivery).select_related('batch__revision__document').first()
    reminder = None
    if not notice:
        reminder = PortalReviewReminder.objects.filter(delivery=delivery).select_related(
            'invitation__batch__revision__document').first()
        notice = reminder.invitation if reminder else None
    if not notice:
        return False
    allowed = delivery.recipient == notice.email and invitation_eligible(notice, reminder=reminder is not None)
    if not allowed:
        delivery.state, delivery.body, delivery.html_body = 'canceled', '', ''
        delivery.save(update_fields=['state', 'body', 'html_body'])
    return not allowed


@transaction.atomic
def configure_reminders(invitation_id, *, project, version, enabled, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    notice = PortalInvitation.objects.select_for_update(of=('self',)).select_related('batch__revision__document').get(
        pk=invitation_id, batch__project=project)
    if notice.version != version:
        raise EditConflict('Reminder settings changed. Reload before saving.')
    if enabled and (not notice.review_requested or not invitation_eligible(notice)):
        raise ValidationError('This invitation is no longer eligible. Send an invitation for the current plan and audience.')
    if enabled and (notice.reminders.filter(number=2).exists()
            or notice.reminders.exclude(delivery__state='sent').exists()):
        raise ValidationError('This reminder sequence is complete or needs attention. Send a new reviewed invitation if another review is needed.')
    notice.reminders_enabled = bool(enabled)
    notice.version += 1
    notice.save(update_fields=['reminders_enabled', 'version'])
    if not enabled:
        NotificationDelivery.objects.filter(portal_review_reminder__invitation=notice, state='pending').update(
            state='canceled', body='', html_body='')
    event(project, actor, 'portal_review_reminders', invitation=notice.pk, enabled=bool(enabled))
    return notice


def queue_review_reminders(limit=100):
    # Both boundaries serialize with grant changes, submission and other workers.
    with transaction.atomic():
        lock_queue()
        now = timezone.now()
        notices = PortalInvitation.objects.filter(reminders_enabled=True, review_requested=True,
            delivery__state='sent', delivery__sent_at__lte=now-timedelta(days=3)).exclude(reminders__number=2).select_related(
                'delivery', 'batch__revision__document').order_by('delivery__sent_at', 'pk')[:limit]
        queued = 0
        for notice in notices:
            if not invitation_eligible(notice, reminder=True):
                notice.reminders_enabled = False
                notice.version += 1
                notice.save(update_fields=['reminders_enabled', 'version'])
                continue
            prior = list(notice.reminders.select_related('delivery').order_by('number'))
            if len(prior) >= 2:
                continue
            if prior and (prior[0].delivery.state != 'sent' or not prior[0].delivery.sent_at
                    or max(notice.delivery.sent_at+timedelta(days=7), prior[0].delivery.sent_at+timedelta(days=3)) > now):
                continue
            # Only one reminder per cycle, even after a worker outage.
            number = len(prior)+1
            delivery = NotificationDelivery.objects.create(recipient_kind='client', recipient=notice.email,
                subject='Reminder: your plans are ready for review',
                body=invitation_body(notice.batch.project, notice.batch.revision,
                    'We have not received your review of this plan version yet. When you are ready, open the plan '
                    'and submit your requested changes. Contact us if no changes are needed, '
                    'you need more time, or you would prefer no reminders.'),
                provider_reference='portal-reminder-'+uuid.uuid4().hex)
            PortalReviewReminder.objects.create(invitation=notice, number=number, delivery=delivery)
            queued += 1
        return queued
