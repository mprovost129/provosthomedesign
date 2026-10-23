import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from .models import Client, WorkItem, WorkProject, Attachment, ClientEmail, CLOSED_STATUSES
from .pm_models import ProjectPlan, ProjectTask, Proposal, ProposalRevision, ProposalTemplate, ProposalInvitation, ProjectEvent
from .pm_forms import PlanForm, TaskForm, ProposalForm, LineFormSet, SendForm, TemplateForm, ResponseForm, draft_data
from .pm_services import (save_plan, save_task, change_task, save_proposal, send_proposal, revise_proposal,
                          withdraw_proposal, snapshot_for, valid_invitation, respond, current_client, event)
from .services import EditConflict, lock_queue
from .views import queue_enabled, staff_preview
from .crm import require_staff
from .pm_pdf import proposal_pdf


def staff_view(view):
    return queue_enabled(staff_member_required(login_url='workqueue:staff_login')(
        permission_required('workqueue.view_workitem', raise_exception=True)(never_cache(view))))


def editable(request):
    if staff_preview(request):
        raise PermissionDenied
    require_staff(request.user, 'workqueue.change_workitem')


def error_text(exc):
    return '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def query_uuid(value):
    try:
        return forms.UUIDField().clean(value)
    except ValidationError:
        raise Http404 from None


def client_initial(project, proposal=None):
    if proposal:
        return current_client(proposal.client)
    item = WorkItem.objects.filter(project=project, client_contact__isnull=False).select_related('client_contact__client').first()
    return item.client_contact.client if item else None


@staff_view
@require_http_methods(['GET'])
def projects(request):
    q = request.GET.get('q', '').strip()[:200]
    scope = request.GET.get('scope', 'active')
    visible = Q(work_items__deleted_at__isnull=True, work_items__id__isnull=False)
    queryset = WorkProject.objects.annotate(job_count=Count('work_items', filter=visible, distinct=True),
        active_count=Count('work_items', filter=visible & ~Q(work_items__status__in=CLOSED_STATUSES), distinct=True)).filter(job_count__gt=0)
    if scope != 'all':
        scope = 'active'
        queryset = queryset.filter(active_count__gt=0)
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(street__icontains=q) | Q(canonical_reference__icontains=q)
            | Q(work_items__client_contact__client__name__icontains=q) | Q(work_items__reference__icontains=q)
            | Q(filing_folder__number__icontains=q) | Q(proposals__number__icontains=q)).distinct()
    return render(request, 'workqueue/projects.html', {'page': Paginator(queryset.order_by('name'), 30).get_page(request.GET.get('page')),
        'q':q, 'scope':scope})


@staff_view
@require_http_methods(['GET', 'POST'])
def project_detail(request, project_id):
    project = get_object_or_404(WorkProject, pk=project_id)
    plan = ProjectPlan.objects.filter(project=project).first()
    plan_form = PlanForm(instance=plan, initial={'version':plan.version if plan else 0}, prefix='plan')
    task_form = TaskForm(initial={'token':uuid.uuid4()}, prefix='task')
    status = 200
    if request.method == 'POST':
        editable(request)
        status = 400
        try:
            action = request.POST.get('action')
            if action == 'plan':
                plan_form = PlanForm(request.POST, instance=plan, prefix='plan')
                if not plan_form.is_valid():
                    raise ValidationError('Review the highlighted project fields.')
                save_plan(project.pk, plan_form.cleaned_data['version'], plan_form.cleaned_data, request.user)
            elif action == 'task':
                task_form = TaskForm(request.POST, prefix='task')
                if not task_form.is_valid():
                    raise ValidationError('Review the highlighted task fields.')
                save_task(project.pk, task_form.cleaned_data, request.user)
            elif action in {'complete', 'reopen', 'cancel'}:
                change_task(project.pk, forms.UUIDField().clean(request.POST.get('task_id')),
                    forms.IntegerField(min_value=1).clean(request.POST.get('version')), action, request.user)
            else:
                raise ValidationError('Choose a project action.')
        except (ValidationError, EditConflict, ProjectTask.DoesNotExist) as exc:
            messages.error(request, error_text(exc) if not isinstance(exc, ProjectTask.DoesNotExist) else 'This task is no longer available.')
            status = 409 if isinstance(exc, EditConflict) else 400
        else:
            messages.success(request, 'Project updated.')
            return redirect('workqueue:project_detail', project.pk)
    items = WorkItem.objects.filter(project=project).with_position().select_related('client_contact__client').prefetch_related('submissions__attachments')
    files = Attachment.objects.filter(submission__work_item__in=items).select_related('submission__work_item').order_by('-uploaded_at')
    from .portal_services import related_clients
    clients = related_clients(project)
    proposals = project.proposals.select_related('current_revision', 'client')
    accepted = ProposalRevision.objects.filter(proposal__project=project, state='accepted').order_by('-accepted_at')
    correspondence = ClientEmail.objects.filter(Q(project=project) | Q(work_item__in=items)).select_related('client', 'delivery').order_by('-created_at')[:20]
    from .time_views import timer_context
    from .time_services import time_summary
    return render(request, 'workqueue/project_detail.html', {**timer_context(request.user, project), 'time_totals':time_summary(project), 'project':project, 'plan':plan, 'plan_form':plan_form,
        'task_form':task_form, 'tasks':project.tasks.filter(canceled_at__isnull=True), 'items':items, 'files':files,
        'pricing_calculations':project.pricing_estimates.select_related('current_revision')[:6], 'clients':clients, 'proposals':proposals, 'accepted':accepted, 'correspondence':correspondence,
        'events':project.planning_events.all()[:30], 'can_change':not staff_preview(request) and request.user.has_perm('workqueue.change_workitem')}, status=status)


@staff_view
@require_http_methods(['GET', 'POST'])
def proposal_edit(request, project_id, proposal_id=None):
    editable(request)
    project = get_object_or_404(WorkProject, pk=project_id)
    proposal = get_object_or_404(Proposal, pk=proposal_id, project=project) if proposal_id else None
    if proposal and not proposal.has_draft:
        return redirect('workqueue:proposal_detail', proposal.pk)
    if proposal and proposal.base_revision_id:
        return redirect('workqueue:change_order_edit',proposal.base_revision_id,proposal.pk)
    source = None
    if not proposal and request.GET.get('item'):
        source = get_object_or_404(WorkItem, pk=query_uuid(request.GET['item']), project=project)
    initial = dict(proposal.draft) if proposal else {'title':project.name, 'pricing':'fixed',
        'valid_until': timezone.localdate()+timedelta(days=30), 'scope': source.description if source else getattr(getattr(project, 'plan', None), 'scope', ''),
        'deliverables':'', 'revisions':'', 'exclusions':'', 'terms':''}
    if not proposal:
        initial.update(document_kind='estimate' if request.GET.get('kind') == 'estimate' else 'proposal',issue_date=timezone.localdate())
    if not proposal and request.GET.get('template'):
        template = get_object_or_404(ProposalTemplate, pk=query_uuid(request.GET['template']), archived=False)
        initial.update({name:getattr(template, name) for name in ['scope', 'deliverables', 'revisions', 'exclusions', 'terms']})
    initial.update(client=client_initial(project, proposal), token=proposal.pk if proposal else uuid.uuid4(), version=proposal.version if proposal else 0)
    if not proposal and request.GET.get('client'):
        initial['client'] = query_uuid(request.GET['client'])
    form = ProposalForm(request.POST or None, project=project, proposal=proposal, initial=initial)
    lines = LineFormSet(request.POST or None, prefix='lines', initial=initial.get('lines') or [{'description':source.service_needed if source else '', 'quantity':'1', 'rate':''}])
    status = 200
    if request.method == 'POST':
        status = 400
        valid = form.is_valid()
        valid_lines = lines.is_valid()
        if valid and valid_lines:
            try:
                data = draft_data(form, lines)
                saved = save_proposal(project.pk, form.cleaned_data['client'].pk, data, form.cleaned_data['token'],
                    form.cleaned_data['version'], request.user, proposal.pk if proposal else None, source.pk if source else None)
            except (ValidationError, EditConflict) as exc:
                form.add_error(None, error_text(exc))
                status = 409 if isinstance(exc, EditConflict) else 400
            else:
                messages.success(request, 'Draft saved. Preview it before sending.')
                return redirect('workqueue:proposal_detail', saved.pk)
    from .document_views import editor_context
    return render(request, 'workqueue/document_editor.html', {'project':project, 'proposal':proposal, 'form':form,
        'lines':lines, 'templates':ProposalTemplate.objects.filter(archived=False), **editor_context(form,project)}, status=status)


@staff_view
@require_http_methods(['GET', 'POST'])
def proposal_detail(request, proposal_id):
    proposal = get_object_or_404(Proposal.objects.select_related('client', 'project', 'current_revision'), pk=proposal_id)
    form = SendForm(client=current_client(proposal.client), document_kind=proposal.document_label.lower(), initial={'token':uuid.uuid4(), 'version':proposal.version})
    status = 200
    if request.method == 'POST':
        editable(request)
        status = 400
        try:
            action = request.POST.get('action')
            if action == 'send':
                form = SendForm(request.POST, client=current_client(proposal.client), document_kind=proposal.document_label.lower())
                if not form.is_valid():
                    raise ValidationError('Review the recipient and confirmation checkbox.')
                send_proposal(proposal.pk, form.cleaned_data['version'], form.cleaned_data['contact'].pk, form.cleaned_data['token'], request.user)
                messages.success(request, 'Proposal queued for email. Its sent version is now fixed.')
            elif action == 'revise':
                revise_proposal(proposal.pk, forms.IntegerField(min_value=1).clean(request.POST.get('version')), request.user)
                return redirect('workqueue:proposal_edit', proposal.project_id, proposal.pk)
            elif action == 'withdraw':
                withdraw_proposal(proposal.pk, forms.IntegerField(min_value=1).clean(request.POST.get('version')), request.user)
                messages.success(request, 'This version is withdrawn and cannot be accepted.')
            else:
                raise ValidationError('Choose a proposal action.')
        except (ValidationError, EditConflict) as exc:
            form.add_error(None, error_text(exc))
            status = 409 if isinstance(exc, EditConflict) else 400
        else:
            return redirect('workqueue:proposal_detail', proposal.pk)
    snapshot = snapshot_for(proposal) if proposal.has_draft else proposal.current_revision.snapshot
    from .change_orders import amounts
    change_orders=Proposal.objects.filter(base_revision__proposal=proposal).select_related('current_revision','base_revision').order_by('number')
    agreement_summary=amounts(proposal.current_revision) if not proposal.base_revision_id and proposal.current_revision and proposal.current_revision.state=='accepted' else None
    revision_locked=bool((proposal.base_revision_id and proposal.current_revision and proposal.current_revision.state=='accepted') or change_orders.filter(current_revision__state__in=['sent','viewed','changes','accepted']).exists())
    return render(request, 'workqueue/proposal_detail.html', {'proposal':proposal, 'snapshot':snapshot, 'send_form':form,
        'history':proposal.revisions.all(), 'billing_client':current_client(proposal.client),
        'change_orders':change_orders,'agreement_summary':agreement_summary,
        'revision_locked':revision_locked,
        'invoice':getattr(proposal, 'billing_invoice', None), 'can_change':not staff_preview(request) and request.user.has_perm('workqueue.change_workitem')}, status=status)


@staff_view
@require_http_methods(['GET'])
def proposal_download(request, proposal_id):
    proposal = get_object_or_404(Proposal.objects.select_related('client', 'project', 'current_revision'), pk=proposal_id)
    revision = None
    if request.GET.get('revision'):
        revision = get_object_or_404(ProposalRevision, pk=query_uuid(request.GET['revision']), proposal=proposal)
    snapshot = revision.snapshot if revision else snapshot_for(proposal) if proposal.has_draft else proposal.current_revision.snapshot
    response = HttpResponse(proposal_pdf(snapshot, revision or (None if proposal.has_draft else proposal.current_revision)), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{proposal.number}.pdf"'
    return response


@staff_view
@require_http_methods(['GET', 'POST'])
def proposal_templates(request, template_id=None):
    editable(request)
    template = get_object_or_404(ProposalTemplate, pk=template_id) if template_id else None
    form = TemplateForm(request.POST or None, instance=template, initial={'version':template.version if template else 0})
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            from django.db import transaction
            try:
                with transaction.atomic():
                    lock_queue()
                    current = ProposalTemplate.objects.filter(pk=template_id).first() if template_id else None
                    if (current.version if current else 0) != form.cleaned_data['version']:
                        raise EditConflict('This template changed. Reload before saving.')
                    saved = form.save(commit=False)
                    saved.version = (current.version if current else 0) + 1
                    saved.save()
            except EditConflict as exc:
                form.add_error(None, str(exc))
                status = 409
            else:
                messages.success(request, 'Reusable proposal template saved.')
                return redirect('workqueue:proposal_templates')
    return render(request, 'workqueue/proposal_templates.html', {'form':form, 'template':template, 'templates':ProposalTemplate.objects.all()}, status=status)


def private_response(response, *, has_form=False):
    response['Cache-Control'] = 'no-store, private'
    # Keep private paths off other sites, while allowing same-origin native
    # form POSTs to satisfy Django's HTTPS Origin/Referer checks (including iOS).
    # Invitation tokens stay in fragments and are never included in referrers.
    response['Referrer-Policy'] = 'same-origin' if has_form else 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response


@queue_enabled
@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='20/h', method='POST', block=False)
def proposal_access(request):
    error, status = '', 200
    if request.method == 'POST':
        status = 400
        raw = request.POST.get('token', '')
        if getattr(request, 'limited', False):
            error, status = 'Too many attempts. Please try again later.', 429
        elif 20 <= len(raw) <= 100:
            digest = hashlib.sha256(raw.encode()).hexdigest()
            invite = ProposalInvitation.objects.filter(digest=digest, expires_at__gt=timezone.now()).first()
            if invite:
                request.session['proposal_invitation'] = invite.pk
                request.session.cycle_key()
                return private_response(redirect('workqueue:proposal_public', invite.revision_id))
            error = 'This private link is invalid or expired. Contact us for a new link.'
        else:
            error = 'Open the complete private link from your proposal email.'
    return private_response(render(request, 'workqueue/proposal_access.html', {'error':error}, status=status), has_form=True)


def public_invite(request, revision_id):
    try:
        invite = valid_invitation(request.session.get('proposal_invitation'))
    except ValidationError:
        return None
    return invite if invite.revision_id == revision_id else None


@queue_enabled
@require_http_methods(['GET', 'POST'])
def proposal_public(request, revision_id):
    invite = public_invite(request, revision_id)
    if not invite:
        return private_response(render(request, 'workqueue/proposal_access.html', {'error':'Open the private link in your proposal email to view this document.'}, status=403), has_form=True)
    revision = invite.revision
    proposal = revision.proposal
    available = proposal.current_revision_id == revision.pk and not proposal.has_draft and revision.state in {'sent', 'viewed'}
    if available and proposal.base_revision_id:
        from .change_orders import check_send
        try:check_send(proposal)
        except ValidationError:available=False
    form = ResponseForm(request.POST or None, initial={'action':'accept'}, document_kind=revision.snapshot.get('document_kind','proposal'))
    error, status = '', 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                respond(invite.pk, form.cleaned_data)
            except ValidationError as exc:
                error = error_text(exc)
            else:
                return private_response(redirect('workqueue:proposal_public', revision.pk))
    elif available and not revision.viewed_at:
        from django.db import transaction
        with transaction.atomic():
            lock_queue()
            ProposalRevision.objects.filter(pk=revision.pk, state='sent', viewed_at__isnull=True).update(state='viewed', viewed_at=timezone.now())
        revision.refresh_from_db()
    return private_response(render(request, 'workqueue/proposal_public.html', {'snapshot':revision.snapshot, 'revision':revision,
        'form':form, 'error':error, 'available':available}, status=status), has_form=True)


@queue_enabled
@require_http_methods(['GET'])
def proposal_public_pdf(request, revision_id):
    invite = public_invite(request, revision_id)
    if not invite:
        return private_response(HttpResponse('Open your private proposal link first.', status=403))
    response = HttpResponse(proposal_pdf(invite.revision.snapshot, invite.revision), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{invite.revision.proposal.number}.pdf"'
    return private_response(response)
