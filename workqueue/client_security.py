"""Bot checks for public client writes; never silently disable them in production."""
from django.conf import settings
from django.core.exceptions import ValidationError

from core.utils import verify_recaptcha_v3


VERIFICATION_ERROR = "We could not verify this request. Please try again. If it continues, contact Provost Home Design."


def recaptcha_configuration():
    site = (getattr(settings, "RECAPTCHA_SITE_KEY", "") or getattr(settings, "RECAPTCHA_PUBLIC_KEY", "")).strip()
    enterprise = bool(getattr(settings, "RECAPTCHA_ENTERPRISE_API_KEY", ""))
    secret = bool(getattr(settings, "RECAPTCHA_SECRET_KEY", "") or getattr(settings, "RECAPTCHA_PRIVATE_KEY", ""))
    ready = bool(site and (getattr(settings, "RECAPTCHA_ENTERPRISE_PROJECT_ID", "") if enterprise else secret))
    # Only the explicitly isolated development workflow may operate without keys.
    local = bool(settings.DEBUG and getattr(settings, "INTAKE_LOCAL_DEVELOPMENT", False) and not enterprise and not secret)
    return {"client_recaptcha_site_key": site if ready else "",
            "client_recaptcha_enterprise": enterprise, "client_recaptcha_local": local}


def require_recaptcha(request, action):
    config = recaptcha_configuration()
    if config["client_recaptcha_local"]:
        return
    if not config["client_recaptcha_site_key"] or not request.POST.get("recaptcha_token", "").strip():
        raise ValidationError(VERIFICATION_ERROR)
    try:
        ok, _ = verify_recaptcha_v3(request, expected_action=action)
    except Exception:
        # Malformed provider responses and service failures must not authorize a write.
        ok = False
    if not ok:
        raise ValidationError(VERIFICATION_ERROR)


def verify_form(request, form, action):
    if not form.is_valid():
        return False
    try:
        require_recaptcha(request, action)
    except ValidationError as exc:
        form.add_error(None, exc)
        return False
    return True
