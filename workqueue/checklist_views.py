import uuid
from datetime import timedelta
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, F, Count, Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import WorkProject, WorkItem, CLOSED_STATUSES
from .pm_models import ProjectTask
from .pm_views import staff_view, editable, error_text
from .pm_services import change_task
from .services import EditConflict
from .views import staff_preview
from .checklist_models import ProjectChecklist
from .checklist_forms import ChecklistForm, ChecklistChoiceForm, ChecklistApplyForm, StepFormSet, TaskEditForm
from .checklist_services import save_checklist, apply_checklist, edit_task, title_key


def can_edit(request):
    return not staff_preview(request) and request.user.has_perm('workqueue.change_workitem')


@staff_view
@require_http_methods(['GET','POST'])
def checklists(request, template_id=None):
    checklist = get_object_or_404(ProjectChecklist, pk=template_id) if template_id else None
    initial = {'token':checklist.pk if checklist else uuid.uuid4(), 'version':checklist.version if checklist else 0,
        'name':checklist.name if checklist else '', 'steps':'\n'.join(checklist.titles) if checklist else '',
        'archived':checklist.archived if checklist else False}
    form = ChecklistForm(request.POST or None, initial=initial)
    status = 200
    if request.method == 'POST':
        editable(request)
        status = 400
        if form.is_valid():
            data = form.cleaned_data
            if checklist and (data['token'] != checklist.pk or data['version'] == 0):
                form.add_error(None, 'Reload this checklist before saving.')
            else:
                try:
                    saved = save_checklist(data['token'], data['version'],
                        dict(name=data['name'],titles=data['steps'],archived=data['archived']), request.user)
                except (ValidationError, EditConflict) as exc:
                    form.add_error(None, error_text(exc))
                    status = 409 if isinstance(exc, EditConflict) else 400
                else:
                    messages.success(request, 'Checklist saved. Existing project tasks are unchanged.')
                    return redirect('workqueue:checklist_edit', saved.pk)
    return render(request,'workqueue/checklists.html',{'form':form,'checklist':checklist,
        'templates':ProjectChecklist.objects.all(),'can_change':can_edit(request)},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def project_checklist(request, project_id):
    editable(request)
    project = get_object_or_404(WorkProject, pk=project_id)
    choice = ChecklistChoiceForm(request.GET or None)
    apply_form, steps, checklist = None, None, None
    status = 200
    if request.method == 'POST':
        apply_form = ChecklistApplyForm(request.POST)
        steps = StepFormSet(request.POST, prefix='steps')
        status = 400
        valid = apply_form.is_valid()
        valid_steps = steps.is_valid()
        if valid:
            checklist = get_object_or_404(ProjectChecklist, pk=apply_form.cleaned_data['template_id'])
        if valid and valid_steps:
            rows = [row.cleaned_data for row in steps if row.cleaned_data.get('include')]
            try:
                application = apply_checklist(project.pk, checklist.pk, apply_form.cleaned_data['version'],
                    apply_form.cleaned_data['token'], rows, request.user)
            except (ValidationError, EditConflict) as exc:
                apply_form.add_error(None, error_text(exc))
                status = 409 if isinstance(exc, EditConflict) else 400
            else:
                added, skipped = len(application.snapshot['added']), len(application.snapshot['skipped'])
                messages.success(request, f'{added} task(s) added. {skipped} existing step(s) skipped.')
                return redirect(reverse('workqueue:project_detail',args=[project.pk])+'#tasks')
    elif choice.is_bound and choice.is_valid():
        checklist = choice.cleaned_data['template']
        apply_form = ChecklistApplyForm(initial={'token':uuid.uuid4(),'template_id':checklist.pk,'version':checklist.version})
        steps = StepFormSet(prefix='steps',initial=[{'title':title,'include':True} for title in checklist.titles])
    if steps:
        used = {title_key(t) for t in project.tasks.filter(canceled_at__isnull=True).values_list('title',flat=True)}
        for step in steps:
            value = step['title'].value() or ''
            step.already_exists = title_key(value) in used
    return render(request,'workqueue/project_checklist.html',{'project':project,'choice':choice,
        'apply_form':apply_form,'steps':steps,'checklist':checklist},status=status)


def task_filters(request):
    data = request.POST if request.method == 'POST' else request.GET
    scope = data.get('scope','active')
    scope = scope if scope in ('active','all') else 'active'
    view = data.get('view','open')
    view = view if view in ('open','overdue','today','week','undated','completed','removed') else 'open'
    return {'q':data.get('q','').strip()[:200],'scope':scope,'view':view}


@staff_view
@require_http_methods(['GET','POST'])
def task_list(request):
    filters = task_filters(request)
    today = timezone.localdate()
    error, status = '', 200
    if request.method == 'POST':
        editable(request)
        try:
            task_id = forms.UUIDField().clean(request.POST.get('task_id'))
            version = forms.IntegerField(min_value=1).clean(request.POST.get('version'))
            action = request.POST.get('action')
            if action not in ('complete','reopen'):
                raise ValidationError('Choose a task action.')
            task = get_object_or_404(ProjectTask,pk=task_id)
            change_task(task.project_id,task.pk,version,action,request.user)
        except (ValidationError,EditConflict) as exc:
            error = error_text(exc)
            status = 409 if isinstance(exc,EditConflict) else 400
        else:
            messages.success(request,'Task updated. Queue status and order are unchanged.')
            return redirect(reverse('workqueue:task_list')+'?'+urlencode(filters))
    jobs = WorkItem.objects.filter(project_id=OuterRef('project_id'))
    tasks = ProjectTask.objects.select_related('project').alias(has_job=Exists(jobs)).filter(has_job=True)
    if filters['scope'] == 'active':
        tasks = tasks.alias(active_project=Exists(jobs.exclude(status__in=CLOSED_STATUSES))).filter(active_project=True)
    if filters['q']:
        tasks = tasks.filter(Q(title__icontains=filters['q']) | Q(project__name__icontains=filters['q'])
            | Q(project__canonical_reference__icontains=filters['q']))
    pending = Q(completed_at__isnull=True,canceled_at__isnull=True)
    conditions = {'open':pending,'overdue':pending & Q(due_date__lt=today),
        'today':pending & Q(due_date=today), 'week':pending & Q(due_date__gte=today,due_date__lte=today+timedelta(days=6)),
        'undated':pending & Q(due_date__isnull=True),'completed':Q(completed_at__isnull=False,canceled_at__isnull=True),
        'removed':Q(canceled_at__isnull=False)}
    totals = tasks.aggregate(open=Count('pk',filter=pending),overdue=Count('pk',filter=conditions['overdue']),
        today=Count('pk',filter=conditions['today']),week=Count('pk',filter=conditions['week']),undated=Count('pk',filter=conditions['undated']))
    page = Paginator(tasks.filter(conditions[filters['view']]).order_by(F('due_date').asc(nulls_last=True),'created_at','pk'),30).get_page(request.GET.get('page'))
    return render(request,'workqueue/project_tasks.html',{'page':page,'totals':totals,'today':today,
        'filters':filters,'error':error,'can_change':can_edit(request)},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def task_edit(request, task_id):
    editable(request)
    task = get_object_or_404(ProjectTask.objects.select_related('project'),pk=task_id)
    form = TaskEditForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'save' else None,
        instance=task,initial={'version':task.version})
    status, error = 200, ''
    if request.method == 'POST':
        status = 400
        try:
            action = request.POST.get('action')
            if action == 'save':
                if not form.is_valid():
                    raise ValidationError('Review the highlighted task fields.')
                edit_task(task.project_id,task.pk,form.cleaned_data['version'],form.cleaned_data,request.user)
            else:
                version = forms.IntegerField(min_value=1).clean(request.POST.get('version'))
                if action == 'cancel' and request.POST.get('confirm_remove') != 'yes':
                    raise ValidationError('Confirm removal first. You can restore this task later.')
                change_task(task.project_id,task.pk,version,action,request.user)
        except (ValidationError,EditConflict) as exc:
            error = error_text(exc)
            status = 409 if isinstance(exc,EditConflict) else 400
        else:
            messages.success(request,'Task updated. Queue status and order are unchanged.')
            return redirect('workqueue:task_edit',task.pk)
    return render(request,'workqueue/task_edit.html',{'task':task,'form':form,'error':error},status=status)
