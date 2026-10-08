from django.conf import settings
from .client_security import recaptcha_configuration


def client_features(request):
    queue = getattr(settings, "WORK_QUEUE_ENABLED", False)
    intake = queue and getattr(settings, "WORK_INTAKE_ENABLED", False)
    return {"intake_enabled": intake, "work_queue_enabled": queue,
            "booking_enabled": intake and getattr(settings, "BOOKING_ENABLED", False),
            **recaptcha_configuration()}
