# core/context_processors.py
from functools import lru_cache
from django.conf import settings

@lru_cache(maxsize=1)
def _branding_values():
    return {
        "COMPANY_NAME": getattr(settings, "COMPANY_NAME", "Provost Home Design"),
        "CONTACT_EMAIL": getattr(settings, "CONTACT_EMAIL", ""),
        "CONTACT_PHONE": getattr(settings, "CONTACT_PHONE", ""),
        "CONTACT_ADDRESS": getattr(settings, "CONTACT_ADDRESS", ""),
        "BRAND_LOGO_STATIC": getattr(settings, "BRAND_LOGO_STATIC", "images/phdlogo.svg"),
        "TAGLINE": getattr(settings, "TAGLINE", ""),
    }

def branding(request):
    # constant mapping; safe to reuse per-request
    return _branding_values()
