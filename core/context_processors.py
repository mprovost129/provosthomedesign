# core/context_processors.py
from functools import lru_cache
from django.conf import settings


def _parse_address(address: str) -> tuple[str, str, str, str]:
    """Split 'Street, City, ST ZIP' into (street, city, region, postal)."""
    street, city, region, postal = address, "", "", ""
    if address:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 3:
            street = parts[0]
            city = parts[1]
            tail = parts[2].split()
            if len(tail) >= 2:
                region, postal = tail[0], tail[1]
    return street, city, region, postal


@lru_cache(maxsize=1)
def _branding_values():
    address = getattr(settings, "CONTACT_ADDRESS", "")
    street, city, region, postal = _parse_address(address)
    return {
        "COMPANY_NAME": getattr(settings, "COMPANY_NAME", "Provost Home Design"),
        "CONTACT_EMAIL": getattr(settings, "CONTACT_EMAIL", ""),
        "CONTACT_PHONE": getattr(settings, "CONTACT_PHONE", ""),
        "CONTACT_ADDRESS": address,
        "CONTACT_STREET": street,
        "CONTACT_CITY": city,
        "CONTACT_REGION": region,
        "CONTACT_POSTAL": postal,
        "CONTACT_COUNTRY": "US",
        "BRAND_LOGO_STATIC": getattr(settings, "BRAND_LOGO_STATIC", "images/phdlogo.svg"),
        "TAGLINE": getattr(settings, "TAGLINE", ""),
    }

def branding(request):
    # constant mapping; safe to reuse per-request
    return _branding_values()
