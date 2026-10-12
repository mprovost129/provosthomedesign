"""Project-scoped portal views; no provider writes, mail, or grants on GET."""
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import new_intake_token, read_intake_token, session_digest, verified_email
from .client_security import verify_form
from .client_views import intake_enabled, limited
from .invoice_history_models import HistoricalInvoice
from .invoice_overview import invoice_queryset, STATUS_LABELS
from .models import Client, PendingUpload, WorkProject
from .notifications import queue_signin, site_url
from .pm_pdf import proposal_pdf
from .pm_views import editable, error_text, private_response, staff_view
from .views import staff_preview
from .portal_forms import ClientProjectForm, MembershipForm, PortalSigninForm, PublicationForm
from .portal_models import ProjectPortal, PortalDocument, PortalDocumentVersion, PortalMember
from .portal_services import (billing_client_ids, document_visibility, grant_member, link_project_client, memberships,
                              publish_document, revoke_member, visible_documents, visible_proposals)
from .services import EditConflict
from .uploads import private_storage


def private_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        return private_response(view(request, *args, **kwargs), has_form=True)
    return wrapped


@intake_enabled
@private_view
@require_http_methods(['GET', 'POST'])
def home(request):
    email = verified_email(request)
    form = PortalSigninForm(request.POST or None)
    sent, status = False, 200
    if request.method == 'POST':
        if verify_form(request, form, 'work_tracking_signin'):
            target = form.cleaned_data['email'].strip().lower()
            allowed = limited(request, 'signin-ip', limit=20, window=900)
            allowed = limited(request, 'signin-email', target, limit=5, window=900) and allowed
            if allowed and memberships(target).exists():
                queue_signin(target, destination='portal')
            sent = True  # Never reveal membership or rate-limit state for an address.
        else:
            status = 400
    return render(request, 'workqueue/portal_home.html', {'client_email': email, 'form': form,
        'memberships': memberships(email), 'sent': sent}, status=status)


def member_for(request, project_id):
    return get_object_or_404(memberships(verified_email(request)), project_id=project_id)


def financial_records(member):
    if not member.financial_access:
        return [], []
    ids = billing_client_ids(member)
    # Drafts, unreconciled writes, voided/deleted invoices and unrecognized provider states stay private.
    sent_states = ['sent', 'viewed', 'paid', 'autopaid', 'partial', 'deposit-paid', 'deposit-partial', 'overdue', 'resolved']
    invoices = list(invoice_queryset().filter(effective_project_id=member.project_id, state='linked',
        invoice_id__isnull=False, last_sync_at__isnull=False, remote_status__in=sent_states).filter(
            Q(client_id__in=ids) | Q(proposal__client_id__in=ids)).order_by('-created_at')[:100])
    for invoice in invoices:
        invoice.portal_status = STATUS_LABELS[invoice.remote_status]
    history = list(HistoricalInvoice.objects.filter(project=member.project, client_id__in=ids,
        removed_at__isnull=True, batch__removed_at__isnull=True, batch__applied_at__isnull=False).annotate(
            exported_state=Lower(Trim('exported_status'))).filter(exported_state__in=sent_states).select_related('live_balance').order_by('-issued')[:100])
    # A CSV copy of an app-created invoice must not appear as a second bill.
    linked_numbers = {(invoice.account_id, invoice.invoice_number) for invoice in invoices}
    history = [record for record in history if (record.account_id, record.number) not in linked_numbers]
    for record in history:
        balance = getattr(record, 'live_balance', None)
        record.portal_balance = balance if balance and balance.verified_at and not balance.error and not balance.archived else None
    return invoices, history


@intake_enabled
@private_view
@require_http_methods(['GET'])
def project(request, project_id):
    if not verified_email(request):
        # Remember only a UUID, never an arbitrary return URL or unverified project details.
        request.session['portal_return_project'] = str(project_id)
        return redirect('workqueue:portal_home')
    member = member_for(request, project_id)
    documents = list(visible_documents(member))
    for document in documents:
        document.history = list(document.revisions.all())
        document.latest = document.history[0] if document.history else None
    invoices, history = financial_records(member)
    return render(request, 'workqueue/portal_project.html', {'project': member.project, 'member': member,
        'client_email': member.email, 'documents': documents, 'proposals': visible_proposals(member),
        'has_general_documents': any(doc.kind != 'invoice' for doc in documents),
        'invoices': invoices, 'invoice_history': history})


@intake_enabled
@private_view
@require_http_methods(['GET'])
def document(request, project_id, version_id):
    member = member_for(request, project_id)
    revision = get_object_or_404(PortalDocumentVersion.objects.select_related('document'), pk=version_id,
        document__in=visible_documents(member))
    if revision.external_url:
        # The landing page explains Google permissions and mutable external content.
        return render(request, 'workqueue/portal_external.html', {'project': member.project,
            'revision': revision, 'client_email': member.email})
    try:
        content = private_storage().open(revision.storage_key, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404 from None
    inline = request.GET.get('view') == '1' and revision.content_type == 'application/pdf'
    response = FileResponse(content, as_attachment=not inline, filename=revision.original_name,
        content_type='application/pdf' if inline else 'application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@intake_enabled
@private_view
@require_http_methods(['GET'])
def proposal(request, project_id, revision_id):
    member = member_for(request, project_id)
    revision = get_object_or_404(visible_proposals(member), pk=revision_id)
    if request.GET.get('download') == '1':
        response = HttpResponse(proposal_pdf(revision.snapshot, revision), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{revision.proposal.number}-v{revision.sequence}.pdf"'
        return response
    return render(request, 'workqueue/portal_proposal.html', {'project': member.project,
        'revision': revision, 'snapshot': revision.snapshot, 'client_email': member.email})


@intake_enabled
@private_view
@require_http_methods(['POST'])
def sign_out(request):
    for key in ['queue_verified_email', 'queue_email_verified_until', 'portal_return_project', 'proposal_invitation']:
        request.session.pop(key, None)
    request.session.cycle_key()
    return redirect('workqueue:portal_home')


@staff_view
@require_http_methods(['GET', 'POST'])
def manage(request, project_id):
    project = get_object_or_404(WorkProject, pk=project_id)
    portal = ProjectPortal.objects.filter(project=project).first()
    version = portal.version if portal else 1
    action = request.POST.get('action', '') if request.method == 'POST' else ''
    access_form = MembershipForm(request.POST if action == 'grant' else None, project=project, initial={'version': version}, auto_id='access_%s')
    client_form = ClientProjectForm(request.POST if action == 'link_client' else None, initial={'version': version}, auto_id='link_%s')
    publication = PublicationForm(request.POST if action == 'publish' else None, project=project,
        initial={'version': version, 'intake_token': new_intake_token(request), 'kind': 'plan'}, auto_id='publication_%s')
    status = 200
    if request.method == 'POST':
        editable(request)
        try:
            if action == 'link_client':
                if not client_form.is_valid():
                    raise ValidationError('Choose the client and confirm the project connection.')
                link_project_client(project, version=client_form.cleaned_data['version'],
                    client_id=client_form.cleaned_data['client'].pk, actor=request.user)
                messages.success(request, 'Client linked. You can now approve a contact’s portal access below.')
            elif action == 'grant':
                if not access_form.is_valid():
                    raise ValidationError('Review the client, permissions, and approval checkbox.')
                data = access_form.cleaned_data
                grant_member(project, version=data['version'], contact_id=data['contact'].pk,
                    financial_access=data['financial_access'], actor=request.user)
                messages.success(request, 'Project access saved. Copy the project link below to share it with this contact.')
            elif action == 'publish':
                if not publication.is_valid():
                    raise ValidationError('Review the document fields and confirm publication.')
                data = publication.cleaned_data
                nonce = read_intake_token(request, data['intake_token'])
                publish_document(project, version=data['version'], title=data['title'], kind=data['kind'],
                    billing_client_id=data['billing_client'].pk if data['billing_client'] else None,
                    document_id=data['document'].pk if data['document'] else None, note=data['note'],
                    external_url=data['external_url'], upload_ids=data['upload_ids'], nonce=nonce,
                    binding=session_digest(request), actor=request.user)
                messages.success(request, 'Document version published to the client portal.')
            elif action == 'revoke':
                revoke_member(project, version=forms.IntegerField(min_value=1).clean(request.POST.get('version')),
                    member_id=forms.IntegerField(min_value=1).clean(request.POST.get('member')), actor=request.user)
                messages.success(request, 'Portal access revoked for this project.')
            elif action in {'withdraw', 'restore'}:
                document_visibility(project, version=forms.IntegerField(min_value=1).clean(request.POST.get('version')),
                    document_id=forms.UUIDField().clean(request.POST.get('document')), visible=action == 'restore', actor=request.user)
                messages.success(request, 'Document sharing updated.')
            else:
                raise ValidationError('Choose a sharing action.')
        except (ValidationError, EditConflict, Client.DoesNotExist, PortalDocument.DoesNotExist, PortalMember.DoesNotExist) as exc:
            messages.error(request, error_text(exc) if isinstance(exc, (ValidationError, EditConflict)) else 'This sharing record is no longer available.')
            status = 409 if isinstance(exc, EditConflict) else 400
        else:
            return redirect('workqueue:portal_manage', project.pk)
    ready = []
    try:
        token = publication.data.get('intake_token') if publication.is_bound else publication.initial['intake_token']
        nonce = read_intake_token(request, token)
        ready = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=session_digest(request),
            state='ready', expires_at__gt=timezone.now())
    except ValidationError:
        pass
    docs = list(project.portal_documents.prefetch_related('revisions').select_related('billing_client'))
    for doc in docs:
        doc.latest = next(iter(doc.revisions.all()), None)
    return render(request, 'workqueue/portal_manage.html', {'project': project, 'version': version,
        'client_form': client_form, 'access_form': access_form, 'publication': publication, 'documents': docs,
        'members': project.portal_members.select_related('client', 'contact'), 'ready_uploads': ready,
        'project_link': site_url(reverse('workqueue:portal_project', args=[project.pk])),
        'direct_uploads': settings.INTAKE_DIRECT_UPLOADS,
        'can_change': not staff_preview(request) and request.user.has_perm('workqueue.change_workitem')}, status=status)
