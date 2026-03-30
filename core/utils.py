"""
Shared utility functions for the Django project.
"""
from __future__ import annotations
from urllib.parse import urlparse
from typing import Any

from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """
    Get the client's IP address from the request.
    Best-effort IP; Nginx should set X-Forwarded-For
    """
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return xff or (request.META.get("REMOTE_ADDR") or "")


def _normalize_host(value: str) -> str:
    """
    Normalize host names for safe equality checks.
    """
    if not value:
        return ""
    host = (value or "").strip().lower()
    if "://" in host:
        host = urlparse(host).hostname or ""
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def verify_recaptcha_v3(request: HttpRequest, expected_action: str | None = None) -> tuple[bool, float | None]:
    """
    Verify reCAPTCHA v3 token. Returns (ok, score).
    If RECAPTCHA_SECRET_KEY is not configured, treat as "not enforced".
    
    Args:
        request: The HTTP request containing the recaptcha_token in POST data
        
    Returns:
        tuple: (success: bool, score: float | None)
            - success: True if reCAPTCHA passed or not enforced, False otherwise
            - score: The reCAPTCHA score (0.0-1.0) or None if not enforced
    """
    secret = (
        (getattr(settings, "RECAPTCHA_SECRET_KEY", "") or "").strip()
        or (getattr(settings, "RECAPTCHA_PRIVATE_KEY", "") or "").strip()
    )
    if not secret:
        return True, None  # not enforced if not configured

    token = (request.POST.get("recaptcha_token") or "").strip()
    if not token:
        return False, 0.0

    try:
        import requests  # type: ignore
    except Exception:
        # If requests isn't installed, fail closed (spam) when recaptcha is configured
        return False, 0.0

    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": secret,
                "response": token,
                "remoteip": get_client_ip(request),
            },
            timeout=5,
        )
        data: dict[str, Any] = resp.json()
    except Exception:
        return False, 0.0

    success = bool(data.get("success"))
    score = data.get("score")
    action = (data.get("action") or "").strip()
    response_host = _normalize_host(str(data.get("hostname") or ""))
    try:
        score_f = float(score) if score is not None else 0.0
    except Exception:
        score_f = 0.0

    min_score = float(
        getattr(
            settings,
            "RECAPTCHA_MIN_SCORE",
            getattr(settings, "RECAPTCHA_REQUIRED_SCORE", 0.5),
        )
    )
    action_ok = True
    if expected_action:
        action_ok = action == expected_action

    # Accept exact request host and any configured allowed hosts.
    request_host = _normalize_host(request.get_host())
    configured_hosts = [(h or "").strip().lower() for h in getattr(settings, "ALLOWED_HOSTS", [])]

    hostname_ok = False
    if response_host:
        if response_host == request_host:
            hostname_ok = True
        elif "*" in configured_hosts:
            hostname_ok = True
        else:
            for allowed in configured_hosts:
                candidate = _normalize_host(allowed)
                if not candidate:
                    continue
                if allowed.startswith("."):
                    if response_host.endswith(candidate):
                        hostname_ok = True
                        break
                elif response_host == candidate:
                    hostname_ok = True
                    break

    ok = success and score_f >= min_score and action_ok and hostname_ok
    return ok, score_f
