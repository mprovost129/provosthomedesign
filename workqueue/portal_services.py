"""Portal authorization is independent of CRM matching and intake access."""
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access import normalize_email
from .crm import require_staff
from .models import Client, ClientContact, WorkItem
from .pm_models import ProposalRevision
from .pm_services import current_client, event
from .portal_models import ClientProject, ProjectPortal, PortalMember, PortalDocument, PortalDocumentVersion
from .services import EditConflict, lock_queue


def memberships(email):
    if not email:
        return PortalMember.objects.none()
    return PortalMember.objects.filter(email=normalize_email(email), revoked_at__isnull=True).select_related('project', 'client')


def related_clients(project):
    """Require an actual project relationship, not a shared email or company name."""
    ids = set(WorkItem.objects.filter(project=project, client_contact__isnull=False).values_list('client_contact__client_id', flat=True))
    ids.update(project.proposals.values_list('client_id', flat=True))
    ids.update(project.client_links.values_list('client_id', flat=True))
    ids.update(project.historical_invoices.filter(removed_at__isnull=True, batch__removed_at__isnull=True).values_list('client_id', flat=True))
    ids.discard(None)
    resolved = {current_client(client).pk for client in Client.objects.filter(pk__in=ids)}
    return Client.objects.filter(pk__in=resolved, merged_into__isnull=True)


def related_contacts(project):
    return ClientContact.objects.filter(client__in=related_clients(project), archived_at__isnull=True).exclude(email__isnull=True).exclude(email='').select_related('client')


def billing_client_ids(member):
    # A CRM merge must not silently expand a portal billing audience. Staff can
    # review/reapprove the contact under its new company when that is intended.
    return {member.client_id}


def visible_documents(member):
    documents = PortalDocument.objects.filter(project=member.project, withdrawn_at__isnull=True)
    audience = Q(kind__in=['plan', 'document'])
    if member.financial_access:
        audience |= Q(kind='invoice', billing_client_id__in=billing_client_ids(member))
    return documents.filter(audience).prefetch_related('revisions')


def visible_proposals(member):
    if not member.financial_access:
        return ProposalRevision.objects.none()
    # Sent snapshots only; private working drafts and internal project notes never enter the portal.
    return ProposalRevision.objects.filter(proposal__project=member.project,
        proposal__client_id__in=billing_client_ids(member)).select_related('proposal').order_by('-sent_at', '-sequence')


def google_document_url(value):
    """No fetching/embedding arbitrary URLs and no public-sharing changes."""
    value = value.strip()
    parsed = urlsplit(value)
    valid_path = ((parsed.hostname == 'drive.google.com' and re.fullmatch(r'/(file/d/[A-Za-z0-9_-]+(?:/view|/preview)?|drive/(?:u/\d+/)?folders/[A-Za-z0-9_-]+)/?', parsed.path))
                  or (parsed.hostname == 'docs.google.com' and re.fullmatch(r'/(document|spreadsheets|presentation)/d/[A-Za-z0-9_-]+(?:/(edit|view|preview))?/?', parsed.path)))
    if (parsed.scheme != 'https' or parsed.netloc not in {'drive.google.com', 'docs.google.com'}
            or not valid_path or parsed.fragment or any(ord(char) < 32 for char in value) or len(value) > 1000):
        raise ValidationError('Use a full HTTPS Google Drive file/folder or Google document link. Upload a file for a fixed, privately hosted version.')
    return value


def checked_portal(project, version):
    portal, _ = ProjectPortal.objects.get_or_create(project=project)
    if portal.version != version:
        raise EditConflict('Project sharing changed. Reload and review the current recipients and documents.')
    return portal


def changed(portal, actor, action, **details):
    portal.version += 1
    portal.save(update_fields=['version'])
    event(portal.project, actor, action, **details)


@transaction.atomic
def link_project_client(project, *, version, client_id, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    portal = checked_portal(project, version)
    client = Client.objects.get(pk=client_id, merged_into__isnull=True)
    ClientProject.objects.get_or_create(project=project, client=client, defaults={'actor': actor})
    changed(portal, actor, 'Client linked to project', client=str(client.pk), name=client.name)


@transaction.atomic
def grant_member(project, *, version, contact_id, financial_access, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    portal = checked_portal(project, version)
    contact = related_contacts(project).filter(pk=contact_id).first()
    if not contact:
        raise ValidationError('Choose a current contact from a client connected to this project.')
    email = normalize_email(contact.email)
    validate_email(email)
    member, _ = PortalMember.objects.update_or_create(project=project, email=email, defaults={
        'client': contact.client, 'contact': contact, 'financial_access': financial_access,
        'revoked_at': None, 'granted_at': timezone.now(), 'actor': actor})
    changed(portal, actor, 'Client portal access approved', email=email, client=str(contact.client_id), financial_access=financial_access)
    return member


@transaction.atomic
def revoke_member(project, *, version, member_id, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    portal = checked_portal(project, version)
    member = PortalMember.objects.get(pk=member_id, project=project)
    member.revoked_at = timezone.now()
    member.save(update_fields=['revoked_at'])
    changed(portal, actor, 'Client portal access revoked', email=member.email)


@transaction.atomic
def publish_document(project, *, version, title, kind, billing_client_id, document_id,
                     note, external_url, upload_ids, nonce, binding, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    portal = checked_portal(project, version)
    if document_id:
        document = PortalDocument.objects.get(pk=document_id, project=project)
        if document.withdrawn_at:
            raise ValidationError('Restore this document before publishing another version.')
    else:
        if kind not in PortalDocument.Kind.values or not title.strip():
            raise ValidationError('Provide a document title and category.')
        billing_client = None
        if kind == 'invoice':
            billing_client = related_clients(project).filter(pk=billing_client_id).first()
            if not billing_client:
                raise ValidationError('Choose the client whose invoice this is.')
        document = PortalDocument(project=project, title=title.strip(), kind=kind, billing_client=billing_client)
        document.full_clean()
        document.save()
    upload = None
    if bool(external_url) == bool(upload_ids):
        raise ValidationError('Choose exactly one uploaded file or one Google Drive link.')
    if external_url:
        external_url = google_document_url(external_url)
        if document.kind == 'invoice':
            raise ValidationError('Upload the invoice PDF so financial documents stay behind portal access controls.')
    else:
        from .completed_deliveries import ready_files
        uploads = ready_files(nonce=nonce, binding=binding, ids=upload_ids)
        if len(uploads) != 1:
            raise ValidationError('Publish one file per document version.')
        upload = uploads[0]
        if document.kind in {'plan', 'invoice'} and upload.content_type != 'application/pdf':
            raise ValidationError('Publish plans and invoices as PDFs so clients can view and download them.')
    last = document.revisions.order_by('-sequence').first()
    revision = PortalDocumentVersion(document=document, sequence=last.sequence + 1 if last else 1,
        note=note.strip(), actor=actor, external_url=external_url, upload=upload,
        original_name=upload.original_name if upload else '', storage_key=upload.storage_key if upload else '',
        content_type=upload.content_type if upload else '', size_bytes=upload.size_bytes if upload else 0)
    revision.full_clean()
    revision.save()
    if upload:
        upload.state = 'attached'
        upload.save(update_fields=['state'])
    changed(portal, actor, 'Portal document published', document=str(document.pk), title=document.title, sequence=revision.sequence)
    return revision


@transaction.atomic
def document_visibility(project, *, version, document_id, visible, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    portal = checked_portal(project, version)
    document = PortalDocument.objects.get(pk=document_id, project=project)
    document.withdrawn_at = None if visible else timezone.now()
    document.save(update_fields=['withdrawn_at'])
    changed(portal, actor, 'Portal document restored' if visible else 'Portal document withdrawn', document=str(document.pk), title=document.title)
