from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from timetracking.models import ActiveTimer, TimeEntry
import json

@csrf_exempt
@login_required
def timer_status(request):
    """Get the current timer state for the logged-in user."""
    try:
        active = ActiveTimer.objects.get(user=request.user)
        entry = active.time_entry
        return JsonResponse({
            'active': True,
            'project_id': entry.project_id,
            'start_time': entry.start_time.isoformat() if entry.start_time else None,
            'elapsed_seconds': int((timezone.now() - entry.start_time).total_seconds()) if entry.start_time else 0,
        })
    except ActiveTimer.DoesNotExist:
        return JsonResponse({'active': False})

@csrf_exempt
@login_required
def timer_start(request):
    """Start a timer for a project."""
    if request.method == 'POST':
        data = json.loads(request.body.decode())
        project_id = data.get('project_id')
        # Stop any existing timer
        ActiveTimer.objects.filter(user=request.user).delete()
        entry = TimeEntry.objects.create(
            user=request.user,
            project_id=project_id,
            start_time=timezone.now(),
            created_via_timer=True
        )
        active = ActiveTimer.objects.create(user=request.user, time_entry=entry)
        return JsonResponse({'status': 'started', 'project_id': project_id})
    return JsonResponse({'error': 'POST required'}, status=400)

@csrf_exempt
@login_required
def timer_stop(request):
    """Stop the current timer and update elapsed time."""
    if request.method == 'POST':
        try:
            active = ActiveTimer.objects.get(user=request.user)
            active.stop()
            return JsonResponse({'status': 'stopped'})
        except ActiveTimer.DoesNotExist:
            return JsonResponse({'error': 'No active timer'}, status=404)
    return JsonResponse({'error': 'POST required'}, status=400)

@csrf_exempt
@login_required
def timer_reset(request):
    """Reset the timer for the user."""
    if request.method == 'POST':
        ActiveTimer.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'reset'})
    return JsonResponse({'error': 'POST required'}, status=400)
