"""
Context processors for billing app.
Makes system settings available in all templates.
"""
from .models import SystemSettings


def system_settings(request):
    """Add system settings to template context."""
    return {
        'system_settings': SystemSettings.load()
    }
