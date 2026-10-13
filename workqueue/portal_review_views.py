import json
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .access import verified_email
from .client_views import intake_enabled, limited
from .pm_views import editable, error_text, staff_view
from .portal_models import PortalDocumentVersion
from .portal_review_models import PlanReview
from .portal_reviews import check_identity, check_member, mark_reviewed, save_review, start_review, submit_review
from .portal_services import visible_documents
from .portal_views import member_for, private_view
from .services import EditConflict
from .uploads import private_storage


def client_review(request, project_id, review_id, *, editing=False):
    member = member_for(request, project_id)
    record = get_object_or_404(PlanReview.objects.select_related('document_version__document', 'work_item'),
        pk=review_id, member=member, client_id=member.client_id, contact_id=member.contact_id,
        author_email=member.email, document_version__document__in=visible_documents(member))
    member, _ = check_member(member, record.document_version, editing=editing)
    check_identity(record, member, editing=editing)
    return member, record


@intake_enabled
@private_view
@require_http_methods(['GET', 'POST'])
def start(request, project_id, version_id):
    if not verified_email(request):
        request.session['portal_return_project'] = str(project_id)
        return redirect('workqueue:portal_home')
    member = member_for(request, project_id)
    revision = get_object_or_404(PortalDocumentVersion.objects.select_related('document'),
        pk=version_id, document__in=visible_documents(member))
    member, revision = check_member(member, revision)
    error, status = '', 200
    if request.method == 'POST':
        if not limited(request, 'portal-review-start', member.email, limit=20, window=3600):
            error, status = 'Too many reviews started. Please try again later.', 429
        else:
            try:
                token = uuid.UUID(request.POST.get('token', ''))
                record = start_review(member, revision, token)
            except (ValueError, ValidationError) as exc:
                error, status = 'Reload this page before starting a review.', 400
            else:
                return redirect('workqueue:portal_review', project_id, record.pk)
    draft = member.plan_reviews.filter(document_version=revision, grant_at=member.granted_at, submitted_at__isnull=True).first()
    latest = revision.document.revisions.first()
    return render(request, 'workqueue/portal_review_start.html', {'project': member.project, 'revision': revision,
        'client_email': member.email, 'token': draft.pk if draft else uuid.uuid4(), 'draft': draft,
        'older_version': latest.pk != revision.pk, 'latest': latest, 'error': error}, status=status)


def payload_from(request):
    if int(request.headers.get('Content-Length') or 0) > 240000 or len(request.body) > 240000:
        raise ValidationError('This review is too large. Use at most 100 short notes.')
    try:
        return json.loads(request.body)
    except (UnicodeDecodeError, ValueError):
        raise ValidationError('The review could not be read. Keep your notes and try again.') from None


def review_context(record, *, staff=False, can_edit=False):
    project = record.document_version.document.project
    from .portal_export_models import PlanReviewExport
    export_record = PlanReviewExport.objects.filter(review=record).first() if record.submitted_at else None
    current_position = None
    if record.work_item_id:
        from .models import WorkItem
        current_item = WorkItem.objects.with_position().filter(pk=record.work_item_id).first()
        current_position = current_item.position if current_item else None
    pdf_url = reverse('workqueue:staff_review_plan', args=[project.pk, record.pk]) if staff else reverse(
        'workqueue:portal_document', args=[project.pk, record.document_version_id]) + '?view=1'
    return {'project': project, 'review': record, 'client_email': '' if staff else record.author_email,
        'staff_review': staff, 'can_edit': can_edit, 'current_position': current_position,
        'export_record': export_record, 'export_url': reverse('workqueue:staff_marked_plan' if staff else 'workqueue:portal_marked_plan', args=[project.pk,record.pk]),
        'review_data': {'version': record.version, 'summary': record.summary,
            'annotations': record.annotations, 'geometry': record.geometry, 'editable': can_edit,
            'pdf_url': pdf_url, 'save_url': reverse('workqueue:save_plan_review', args=[project.pk, record.pk]),
            'submit_url': reverse('workqueue:submit_plan_review', args=[project.pk, record.pk])},
        'older_version': record.document_version.document.revisions.first().pk != record.document_version_id}


def download_report(record):
    from .portal_review_pdf import review_pdf
    response = HttpResponse(review_pdf(record), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="plan-review-{record.pk}.pdf"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@intake_enabled
@private_view
@require_http_methods(['GET'])
def review(request, project_id, review_id):
    if not verified_email(request):
        request.session['portal_return_project'] = str(project_id)
        return redirect('workqueue:portal_home')
    member, record = client_review(request, project_id, review_id)
    if request.GET.get('report') == '1':
        return download_report(record)
    can_edit = not record.submitted_at and member.review_access
    if can_edit:
        try:
            check_member(member, record.document_version)
            check_identity(record, member)
        except PermissionDenied:
            can_edit = False
    return render(request, 'workqueue/portal_review.html', review_context(record, can_edit=can_edit))


@intake_enabled
@private_view
@require_http_methods(['POST'])
def save(request, project_id, review_id):
    member, _ = client_review(request, project_id, review_id, editing=True)
    if not limited(request, 'portal-review-save', member.email, limit=300, window=900):
        return JsonResponse({'error': 'Saving is temporarily paused. Keep this tab open and retry shortly.'}, status=429)
    try:
        record = save_review(review_id, member, payload_from(request))
    except (ValidationError, EditConflict) as exc:
        return JsonResponse({'error': error_text(exc)}, status=409 if isinstance(exc, EditConflict) else 400)
    return JsonResponse({'version': record.version, 'saved_at': record.updated_at.isoformat()})


@intake_enabled
@private_view
@require_http_methods(['POST'])
def submit(request, project_id, review_id):
    member, record = client_review(request, project_id, review_id, editing=True)
    if request.content_type == 'application/json':
        try:
            record, created = submit_review(review_id, member, payload_from(request))
        except (ValidationError, EditConflict) as exc:
            return JsonResponse({'error': error_text(exc)}, status=409 if isinstance(exc, EditConflict) else 400)
        return JsonResponse({'redirect': reverse('workqueue:portal_review', args=[project_id, record.pk]),
            'reference': record.work_item.reference, 'created': created})
    # An accessible ordinary form can submit general notes if the PDF viewer is unavailable.
    try:
        payload = {'version': int(request.POST.get('version', '0')), 'summary': request.POST.get('summary', ''),
            'annotations': record.annotations, 'geometry': record.geometry}
        record, _ = submit_review(review_id, member, payload)
    except (ValueError, ValidationError, EditConflict) as exc:
        context = review_context(record, can_edit=True)
        context['error'] = error_text(exc) if not isinstance(exc, ValueError) else 'Reload and review the current draft.'
        context['posted_summary'] = request.POST.get('summary', '')[:6000]
        return render(request, 'workqueue/portal_review.html', context, status=409 if isinstance(exc, EditConflict) else 400)
    return redirect('workqueue:portal_review', project_id, record.pk)


@staff_view
@private_view
@require_http_methods(['GET'])
def staff_reviews(request, project_id):
    from .models import WorkProject
    project = get_object_or_404(WorkProject, pk=project_id)
    from django.core.paginator import Paginator
    records = PlanReview.objects.filter(document_version__document__project=project, submitted_at__isnull=False).select_related('work_item', 'document_version')
    return render(request, 'workqueue/portal_staff_reviews.html', {'project': project, 'page': Paginator(records, 25).get_page(request.GET.get('page'))})


def submitted_staff_review(project_id, review_id):
    return get_object_or_404(PlanReview.objects.select_related('document_version__document__project', 'work_item'),
        pk=review_id, document_version__document__project_id=project_id, submitted_at__isnull=False)


@staff_view
@private_view
@require_http_methods(['GET', 'POST'])
def staff_review(request, project_id, review_id):
    record = submitted_staff_review(project_id, review_id)
    if request.method == 'POST':
        editable(request)
        try:
            mark_reviewed(record.pk, int(request.POST.get('version', '0')), request.user)
        except (ValueError, EditConflict) as exc:
            context = review_context(record, staff=True)
            context['error'] = str(exc) if isinstance(exc, EditConflict) else 'Reload this review before saving.'
            return render(request, 'workqueue/portal_review.html', context, status=409)
        return redirect('workqueue:staff_plan_review', project_id, review_id)
    if request.GET.get('report') == '1':
        return download_report(record)
    context = review_context(record, staff=True)
    from .views import staff_preview
    context['can_mark_reviewed'] = not staff_preview(request) and request.user.has_perm('workqueue.change_workitem')
    return render(request, 'workqueue/portal_review.html', context)


@staff_view
@private_view
@require_http_methods(['GET'])
def staff_plan(request, project_id, review_id):
    record = submitted_staff_review(project_id, review_id)
    revision = record.document_version
    try:
        content = private_storage().open(revision.storage_key, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response = FileResponse(content, as_attachment=request.GET.get('download') == '1',
        filename=revision.original_name, content_type='application/pdf')
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['X-Content-Type-Options'] = 'nosniff'
    return response
