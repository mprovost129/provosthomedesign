# pages/context_processors.py
from django.conf import settings

def site_analytics(request):
    return {
        "GA_MEASUREMENT_ID": getattr(settings, "GA_MEASUREMENT_ID", ""),
        # Optional: expose DEBUG if you want to block GA in dev
        "DEBUG": getattr(settings, "DEBUG", False),
    }
