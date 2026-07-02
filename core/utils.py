"""
Shared utility functions for the Django project.
"""
from __future__ import annotations
from urllib.parse import urlparse
from typing import Any
import logging

from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


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


def _check_hostname(response_host: str, request_host: str, configured_hosts: list[str]) -> bool:
    if not response_host:
        return False
    if response_host == request_host:
        return True
    if "*" in configured_hosts:
        return True
    for allowed in configured_hosts:
        candidate = _normalize_host(allowed)
        if not candidate:
            continue
        if allowed.startswith("."):
            if response_host.endswith(candidate):
                return True
        elif response_host == candidate:
            return True
    return False


def verify_recaptcha_v3(request: HttpRequest, expected_action: str | None = None) -> tuple[bool, float | None]:
    """Verify a reCAPTCHA Enterprise token. Returns (ok, score)."""
    api_key = (getattr(settings, "RECAPTCHA_ENTERPRISE_API_KEY", "") or "").strip()
    secret = (
        (getattr(settings, "RECAPTCHA_SECRET_KEY", "") or "").strip()
        or (getattr(settings, "RECAPTCHA_PRIVATE_KEY", "") or "").strip()
    )

    if not api_key and not secret:
        return True, None  # not enforced

    token = (request.POST.get("recaptcha_token") or "").strip()
    if not token:
        logger.warning("reCAPTCHA: no token in POST data")
        return False, 0.0

    try:
        import requests as http  # type: ignore
    except Exception:
        return False, 0.0

    min_score = float(getattr(settings, "RECAPTCHA_MIN_SCORE", getattr(settings, "RECAPTCHA_REQUIRED_SCORE", 0.5)))
    request_host = _normalize_host(request.get_host())
    configured_hosts = [(h or "").strip().lower() for h in getattr(settings, "ALLOWED_HOSTS", [])]

    if api_key:
        # ── reCAPTCHA Enterprise ──────────────────────────────────────────────
        project_id = (getattr(settings, "RECAPTCHA_ENTERPRISE_PROJECT_ID", "") or "").strip()
        site_key = (
            (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip()
            or (getattr(settings, "RECAPTCHA_PUBLIC_KEY", "") or "").strip()
        )
        body: dict[str, Any] = {
            "event": {
                "token": token,
                "siteKey": site_key,
            }
        }
        if expected_action:
            body["event"]["expectedAction"] = expected_action

        try:
            resp = http.post(
                f"https://recaptchaenterprise.googleapis.com/v1/projects/{project_id}/assessments",
                params={"key": api_key},
                json=body,
                timeout=5,
            )
            data: dict[str, Any] = resp.json()
        except Exception:
            logger.exception("reCAPTCHA Enterprise API call failed")
            return False, 0.0

        token_props: dict[str, Any] = data.get("tokenProperties") or {}
        risk: dict[str, Any] = data.get("riskAnalysis") or {}

        valid = bool(token_props.get("valid"))
        score = risk.get("score")
        action = (token_props.get("action") or "").strip()
        response_host = _normalize_host(str(token_props.get("hostname") or ""))

        try:
            score_f = float(score) if score is not None else 0.0
        except Exception:
            score_f = 0.0

        action_ok = (action == expected_action) if expected_action else True
        hostname_ok = _check_hostname(response_host, request_host, configured_hosts)
        ok = valid and score_f >= min_score and action_ok and hostname_ok

        logger.info(
            "reCAPTCHA Enterprise: ok=%s valid=%s score=%.2f min=%.2f "
            "action=%r expected=%r action_ok=%s host=%r req_host=%r hostname_ok=%s reasons=%s",
            ok, valid, score_f, min_score,
            action, expected_action, action_ok,
            response_host, request_host, hostname_ok,
            risk.get("reasons", []),
        )
        return ok, score_f

    else:
        # ── Standard reCAPTCHA v3 (fallback when no Enterprise key) ──────────
        try:
            resp = http.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": secret, "response": token, "remoteip": get_client_ip(request)},
                timeout=5,
            )
            data = resp.json()
        except Exception:
            logger.exception("reCAPTCHA v3 API call failed")
            return False, 0.0

        success = bool(data.get("success"))
        score = data.get("score")
        action = (data.get("action") or "").strip()
        response_host = _normalize_host(str(data.get("hostname") or ""))

        try:
            score_f = float(score) if score is not None else 0.0
        except Exception:
            score_f = 0.0

        action_ok = (action == expected_action) if expected_action else True
        hostname_ok = _check_hostname(response_host, request_host, configured_hosts)
        ok = success and score_f >= min_score and action_ok and hostname_ok

        logger.info(
            "reCAPTCHA v3: ok=%s success=%s score=%.2f min=%.2f "
            "action=%r expected=%r action_ok=%s host=%r req_host=%r hostname_ok=%s errors=%s",
            ok, success, score_f, min_score,
            action, expected_action, action_ok,
            response_host, request_host, hostname_ok,
            data.get("error-codes", []),
        )
        return ok, score_f
