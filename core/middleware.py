from __future__ import annotations

from django.conf import settings
from django.urls import get_urlconf, set_urlconf


class SubdomainURLRoutingMiddleware:
    """Use a small, separate URL surface for the temporary web-design site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().partition(":")[0].lower()
        if host == getattr(settings, "WEB_DESIGN_HOST", "web.provosthomedesign.com"):
            request.urlconf = "config.web_urls"
            previous_urlconf = get_urlconf()
            set_urlconf(request.urlconf)
            try:
                return self.get_response(request)
            finally:
                set_urlconf(previous_urlconf)
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """
    Adds a Content-Security-Policy header in production.
    Skipped when DEBUG=True to avoid friction during development.

    Extend CSP_EXTRA_* settings (lists of strings) in settings.py or .env
    to allow additional sources without editing this file.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._policy = self._build() if not settings.DEBUG else None

    def __call__(self, request):
        response = self.get_response(request)
        if self._policy:
            response.setdefault("Content-Security-Policy", self._policy)
        return response

    def _build(self) -> str:
        extra = lambda key: getattr(settings, key, [])  # noqa: E731

        directives: dict[str, list[str]] = {
            "default-src": ["'self'"],
            "script-src": [
                "'self'",
                "cdn.jsdelivr.net",          # Bootstrap JS
                "www.google.com",             # reCAPTCHA
                "www.gstatic.com",            # reCAPTCHA
                "www.googletagmanager.com",   # GA4
                "'unsafe-inline'",            # inline GA/reCAPTCHA/carousel scripts
                *extra("CSP_EXTRA_SCRIPT_SRC"),
            ],
            "style-src": [
                "'self'",
                "cdn.jsdelivr.net",           # Bootstrap CSS + Icons
                "'unsafe-inline'",            # inline style attributes
                *extra("CSP_EXTRA_STYLE_SRC"),
            ],
            "img-src": [
                "'self'",
                "data:",
                "*.amazonaws.com",            # S3 media
                "m.media-amazon.com",         # Amazon affiliate images
                "images-na.ssl-images-amazon.com",
                "ws-na.amazon-adsystem.com",
                "cdn.jsdelivr.net",           # DevIcons on web design page
                *extra("CSP_EXTRA_IMG_SRC"),
            ],
            "font-src": [
                "'self'",
                "cdn.jsdelivr.net",           # Bootstrap Icons webfont
                *extra("CSP_EXTRA_FONT_SRC"),
            ],
            "frame-src": [
                "www.google.com",             # reCAPTCHA iframe
                *extra("CSP_EXTRA_FRAME_SRC"),
            ],
            "connect-src": [
                "'self'",
                "www.google-analytics.com",  # GA4 collection
                "region1.google-analytics.com",
                "www.googletagmanager.com",
                "www.google.com",             # reCAPTCHA verification
                "www.gstatic.com",
                *extra("CSP_EXTRA_CONNECT_SRC"),
            ],
            "base-uri":    ["'self'"],
            "form-action": ["'self'"],
        }

        return "; ".join(
            f"{directive} {' '.join(sources)}"
            for directive, sources in directives.items()
        )
