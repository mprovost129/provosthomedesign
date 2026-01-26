"""
Context processors for billing app.
Makes system settings and active timer available in all templates.
"""
from .models import SystemSettings


def system_settings(request):
    """Add system settings to template context."""
    context = {
        'system_settings': SystemSettings.load()
    }
    
    # Add active timer if user is authenticated
    if request.user.is_authenticated:
        try:
            from timetracking.models import ActiveTimer
            active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=request.user)
            context['active_timer'] = active_timer
        except ActiveTimer.DoesNotExist:
            context['active_timer'] = None
    else:
        context['active_timer'] = None
    
    return context
