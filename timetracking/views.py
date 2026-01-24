from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Q, F
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta
import json

from .models import TimeEntry, ActiveTimer
from .forms import TimeEntryForm, QuickTimerForm
from billing.models import Project


@staff_member_required(login_url='/portal/login/')
def time_dashboard(request):
    """Main time tracking dashboard showing today's entries and timer."""
    user = request.user
    
    # Get or create active timer
    try:
        active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=user)
    except ActiveTimer.DoesNotExist:
        active_timer = None
    
    # Get today's entries
    today = timezone.now().date()
    today_entries = TimeEntry.objects.filter(
        user=user,
        start_time__date=today
    ).select_related('project').order_by('-start_time')
    
    # Calculate today's total time
    today_total = timedelta()
    for entry in today_entries:
        if entry.duration:
            today_total += entry.duration
        elif entry.is_running():
            today_total += timezone.now() - entry.start_time
    
    # Group entries by project for today
    projects_today = {}
    for entry in today_entries:
        project_name = entry.project.job_name
        if project_name not in projects_today:
            projects_today[project_name] = {
                'project': entry.project,
                'entries': [],
                'total': timedelta()
            }
        projects_today[project_name]['entries'].append(entry)
        if entry.duration:
            projects_today[project_name]['total'] += entry.duration
        elif entry.is_running():
            projects_today[project_name]['total'] += timezone.now() - entry.start_time
    
    # Quick timer form
    timer_form = QuickTimerForm()
    
    context = {
        'active_timer': active_timer,
        'today_entries': today_entries,
        'projects_today': projects_today,
        'today_total': today_total,
        'timer_form': timer_form,
    }
    
    return render(request, 'timetracking/dashboard.html', context)


@staff_member_required(login_url='/portal/login/')
def time_entries_list(request):
    """List all time entries with filtering options."""
    user = request.user
    
    # Get filter parameters
    project_id = request.GET.get('project')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    billable = request.GET.get('billable')
    
    # Base queryset
    entries = TimeEntry.objects.filter(user=user).select_related('project')
    
    # Apply filters
    if project_id:
        entries = entries.filter(project_id=project_id)
    if start_date:
        entries = entries.filter(start_time__date__gte=start_date)
    if end_date:
        entries = entries.filter(start_time__date__lte=end_date)
    if billable == 'true':
        entries = entries.filter(is_billable=True)
    elif billable == 'false':
        entries = entries.filter(is_billable=False)
    
    # Calculate totals
    total_duration = timedelta()
    for entry in entries:
        if entry.duration:
            total_duration += entry.duration
    
    # Get all projects for filter dropdown
    projects = Project.objects.filter(status='active').order_by('job_name')
    
    context = {
        'entries': entries,
        'projects': projects,
        'total_duration': total_duration,
        'filters': {
            'project_id': project_id,
            'start_date': start_date,
            'end_date': end_date,
            'billable': billable,
        }
    }
    
    return render(request, 'timetracking/entries_list.html', context)


@staff_member_required(login_url='/portal/login/')
def create_time_entry(request):
    """Create a new time entry manually."""
    if request.method == 'POST':
        form = TimeEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.user = request.user
            entry.created_via_timer = False
            entry.save()
            messages.success(request, 'Time entry created successfully!')
            return redirect('timetracking:time_dashboard')
    else:
        form = TimeEntryForm()
    
    context = {'form': form}
    return render(request, 'timetracking/create_entry.html', context)


@staff_member_required(login_url='/portal/login/')
def edit_time_entry(request, pk):
    """Edit an existing time entry."""
    entry = get_object_or_404(TimeEntry, pk=pk, user=request.user)
    
    if request.method == 'POST':
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, 'Time entry updated successfully!')
            return redirect('timetracking:time_dashboard')
    else:
        form = TimeEntryForm(instance=entry)
    
    context = {'form': form, 'entry': entry}
    return render(request, 'timetracking/edit_entry.html', context)


@staff_member_required(login_url='/portal/login/')
@require_POST
def delete_time_entry(request, pk):
    """Delete a time entry."""
    entry = get_object_or_404(TimeEntry, pk=pk, user=request.user)
    entry.delete()
    messages.success(request, 'Time entry deleted successfully!')
    return redirect('timetracking:time_dashboard')


@staff_member_required(login_url='/portal/login/')
@require_POST
def start_timer(request):
    """Start a new timer."""
    try:
        # Check if user already has an active timer
        existing_timer = ActiveTimer.objects.filter(user=request.user).first()
        if existing_timer:
            return JsonResponse({
                'success': False,
                'error': 'You already have an active timer. Please stop it first.'
            })
        
        # Get project from request
        data = json.loads(request.body)
        project_id = data.get('project_id')
        
        if not project_id:
            return JsonResponse({'success': False, 'error': 'Project is required.'})
        
        project = get_object_or_404(Project, pk=project_id)
        
        # Create new time entry
        time_entry = TimeEntry.objects.create(
            project=project,
            user=request.user,
            start_time=timezone.now(),
            created_via_timer=True
        )
        
        # Create active timer
        active_timer = ActiveTimer.objects.create(
            user=request.user,
            time_entry=time_entry
        )
        
        return JsonResponse({
            'success': True,
            'timer_id': active_timer.id,
            'project_name': project.job_name,
            'start_time': time_entry.start_time.isoformat()
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@staff_member_required(login_url='/portal/login/')
@require_POST
def stop_timer(request):
    """Stop the active timer."""
    try:
        active_timer = ActiveTimer.objects.get(user=request.user)
        active_timer.stop()
        
        return JsonResponse({
            'success': True,
            'duration': active_timer.time_entry.get_duration_display()
        })
    
    except ActiveTimer.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'No active timer found.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@staff_member_required(login_url='/portal/login/')
def get_timer_status(request):
    """Get current timer status (for AJAX polling)."""
    try:
        active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=request.user)
        elapsed = active_timer.get_elapsed_time()
        total_seconds = int(elapsed.total_seconds())
        
        return JsonResponse({
            'active': True,
            'project_name': active_timer.time_entry.project.job_name,
            'project_id': active_timer.time_entry.project.id,
            'start_time': active_timer.time_entry.start_time.isoformat(),
            'elapsed_seconds': total_seconds,
            'elapsed_display': active_timer.time_entry.get_duration_display()
        })
    
    except ActiveTimer.DoesNotExist:
        return JsonResponse({'active': False})


@staff_member_required(login_url='/portal/login/')
def project_time_entries(request, project_id):
    """View time entries for a specific project."""
    project = get_object_or_404(Project, pk=project_id)
    user = request.user
    
    entries = TimeEntry.objects.filter(
        project=project,
        user=user
    ).order_by('-start_time')
    
    # Calculate total time
    total_duration = timedelta()
    billable_duration = timedelta()
    for entry in entries:
        if entry.duration:
            total_duration += entry.duration
            if entry.is_billable:
                billable_duration += entry.duration
    
    context = {
        'project': project,
        'entries': entries,
        'total_duration': total_duration,
        'billable_duration': billable_duration,
    }
    
    return render(request, 'timetracking/project_entries.html', context)

