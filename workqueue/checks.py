from urllib.parse import urlsplit
from django.conf import settings
from django.core.checks import Error, register


@register()
def intake_configuration(app_configs, **kwargs):
    if not getattr(settings, "WORK_INTAKE_ENABLED", False):
        return []
    errors = []
    local = getattr(settings, "INTAKE_LOCAL_DEVELOPMENT", False)
    if not getattr(settings, "WORK_QUEUE_ENABLED", False):
        errors.append(Error("Enable the master queue before enabling public intake.", id="workqueue.E001"))
    if "intake_private" not in settings.STORAGES:
        errors.append(Error("Configure separate private intake storage.", id="workqueue.E002"))
    base = urlsplit(getattr(settings, "INTAKE_PUBLIC_BASE_URL", ""))
    if not base.netloc or (not local and base.scheme != "https"):
        errors.append(Error("Set an HTTPS INTAKE_PUBLIC_BASE_URL for client receipts.", id="workqueue.E003"))
    if not getattr(settings, "INTAKE_OWNER_EMAIL", ""):
        errors.append(Error("Set INTAKE_OWNER_EMAIL for owner notices.", id="workqueue.E004"))
    if not local and (not getattr(settings, "INTAKE_DIRECT_UPLOADS", False)
            or settings.STORAGES.get("intake_private", {}).get("BACKEND") != "storages.backends.s3.S3Storage"):
        errors.append(Error("Production intake requires direct uploads to private persistent S3 storage.", id="workqueue.E005"))
    return errors


@register()
def booking_configuration(app_configs, **kwargs):
    if not getattr(settings, "BOOKING_ENABLED", False):
        return []
    errors = []
    if not getattr(settings, "WORK_INTAKE_ENABLED", False):
        errors.append(Error("Enable verified client intake access before booking.", id="workqueue.E010"))
    if not 1 <= getattr(settings, "BOOKING_HORIZON_DAYS", 60) <= 365:
        errors.append(Error("Booking horizon must be from 1 to 365 days.", id="workqueue.E011"))
    if not getattr(settings, "INTAKE_LOCAL_DEVELOPMENT", False):
        if getattr(settings, "BOOKING_CALENDAR_BACKEND", "google") != "google":
            errors.append(Error("Production booking must use Google Calendar.", id="workqueue.E012"))
        if not all(getattr(settings, name, "") for name in ["GOOGLE_CALENDAR_ID", "GOOGLE_CALENDAR_CLIENT_ID",
                  "GOOGLE_CALENDAR_CLIENT_SECRET", "GOOGLE_CALENDAR_REFRESH_TOKEN"]):
            errors.append(Error("Connect the owner's Google Calendar before enabling bookings.", id="workqueue.E013"))
    return errors
