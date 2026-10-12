"""Web-business identity; intentionally independent of residential SiteSettings."""
from django.conf import settings

NAME = "Slater Woods Design Company"


def public_url(path="/"):
    base = getattr(settings, "WEB_DESIGN_URL", "https://web.provosthomedesign.com")
    return f"{base.rstrip('/')}/{path.lstrip('/')}"
