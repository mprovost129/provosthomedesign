from decimal import InvalidOperation
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import WorkProject
from .pm_views import staff_view, editable, error_text
from .time_models import TimeEntry
from .time_forms import TimerForm, TimeForm
from .time_services import active_timer, start_timer, stop_timer, save_time, archive_time, time_summary
from .services import EditConflict
from .views import staff_preview


def timer_context(actor, project=None):
    return {'timer_form':TimerForm(project=project), 'active_timer':active_timer(actor)}


def state_for(actor):
    entry = active_timer(actor)
    if not entry:
        return {'running':False}
    seconds = max(0, int((timezone.now()-entry.started_at).total_seconds()))
    return {'running':True, 'id':str(entry.pk), 'project':entry.project.name,
        'project_url':reverse('workqueue:project_time', args=[entry.project_id]),
        'job':entry.work_item.reference if entry.work_item else '', 'category':entry.get_category_display(),
        'description':entry.description, 'billable':entry.billable, 'elapsed_seconds':seconds,
        'needs_review':seconds >= 12*3600}


def response_state(request):
    state = state_for(request.user)
    try:
        project_id = forms.UUIDField(required=False).clean(request.GET.get('project'))
    except ValidationError:
        project_id = None
    if project_id:
        project = WorkProject.objects.filter(pk=project_id).first()
        if project:
            state['totals'] = time_summary(project)
    return state


@staff_view
@require_http_methods(['GET', 'POST'])
def timer(request):
    if request.method == 'GET':
        return JsonResponse(response_state(request))
    editable(request)
    json_request = request.headers.get('Accept') == 'application/json'
    status, message = 200, ''
    try:
        action = request.POST.get('action')
        if action == 'start':
            form = TimerForm(request.POST)
            if not form.is_valid():
                raise ValidationError(' '.join(f'{form.fields[name].label or name}: {" ".join(errors)}'
                    for name, errors in form.errors.items() if name in form.fields) or 'Review the timer fields.')
            entry = start_timer(form.cleaned_data['project'].pk, form.cleaned_data, request.user)
            message = 'Timer started.' if entry.running else 'This timer request was already processed.'
        elif action in {'stop', 'discard'}:
            if action == 'discard' and request.POST.get('confirm_discard') != 'yes':
                raise ValidationError('Confirm discarding this timer without recording time.')
            entry_id = forms.UUIDField().clean(request.POST.get('entry_id'))
            entry = stop_timer(entry_id, request.user, discard=action == 'discard')
            message = 'Timer discarded. No time was recorded.' if entry.deleted_at else f'{entry.duration_label} recorded for {entry.project.name}.'
            if (entry.duration_seconds or 0) >= 12*3600:
                message += ' This is over 12 hours; review and correct it if you forgot to stop the timer.'
            if action == 'stop' and request.GET.get('project'):
                message += ' Refresh this page after saving any edits to see the updated entry list.'
        else:
            raise ValidationError('Choose a timer action.')
    except (ValidationError, EditConflict, TimeEntry.DoesNotExist, InvalidOperation) as exc:
        status = 409 if isinstance(exc, EditConflict) else 400
        message = 'This timer is not available to your account.' if isinstance(exc, TimeEntry.DoesNotExist) else error_text(exc)
    if json_request:
        return JsonResponse({**response_state(request), 'message':message}, status=status)
    (messages.success if status == 200 else messages.error)(request, message)
    project_id = request.POST.get('return_project')
    if project_id:
        try:
            return redirect('workqueue:project_time', forms.UUIDField().clean(project_id))
        except ValidationError:
            pass
    return redirect('workqueue:queue')


@staff_view
@require_http_methods(['GET', 'POST'])
def project_time(request, project_id):
    project = get_object_or_404(WorkProject, pk=project_id)
    entry, status = None, 200
    entry_id = request.GET.get('entry') if request.method == 'GET' else request.POST.get('token') if request.POST.get('action') == 'save' else None
    if entry_id:
        try:
            entry_id = forms.UUIDField().clean(entry_id)
        except ValidationError:
            raise Http404 from None
        entry = TimeEntry.objects.filter(pk=entry_id, project=project, duration_seconds__isnull=False, deleted_at__isnull=True).first()
        if request.method == 'GET' and entry is None:
            raise Http404
    form = TimeForm(project=project, entry=entry)
    if request.method == 'POST':
        editable(request)
        status = 400
        try:
            action = request.POST.get('action')
            if action == 'save':
                form = TimeForm(request.POST, project=project, entry=entry)
                if not form.is_valid():
                    raise ValidationError('Review the highlighted time fields.')
                save_time(project.pk, form.cleaned_data, request.user)
            elif action in {'remove', 'restore'}:
                if request.POST.get('confirm') != 'yes':
                    raise ValidationError('Confirm updating this time record.')
                archive_time(project.pk, forms.UUIDField().clean(request.POST.get('entry_id')),
                    forms.IntegerField(min_value=1).clean(request.POST.get('version')), request.user, restore=action == 'restore')
            else:
                raise ValidationError('Choose a time-entry action.')
        except (ValidationError, EditConflict, TimeEntry.DoesNotExist) as exc:
            messages.error(request, 'This time entry is no longer available.' if isinstance(exc, TimeEntry.DoesNotExist) else error_text(exc))
            status = 409 if isinstance(exc, EditConflict) else 400
        else:
            messages.success(request, 'Time record saved. Billing and queue order are unchanged.')
            return redirect(reverse('workqueue:project_time', args=[project.pk]) + ('?show=removed' if action == 'remove' else ''))
    entries = project.time_entries.filter(duration_seconds__isnull=False).select_related('actor', 'work_item')
    removed = request.GET.get('show') == 'removed'
    entries = entries.filter(deleted_at__isnull=not removed)
    return render(request, 'workqueue/project_time.html', {'project':project, 'time_form':form, 'editing_entry':entry,
        'time_totals':time_summary(project), 'removed':removed, 'page':Paginator(entries, 30).get_page(request.GET.get('page')),
        'can_change':not staff_preview(request)
            and request.user.has_perm('workqueue.change_workitem'), **timer_context(request.user, project)}, status=status)
