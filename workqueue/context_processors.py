from django.conf import settings


def client_features(request):
    return {"booking_enabled": getattr(settings, "BOOKING_ENABLED", False) and getattr(settings, "WORK_INTAKE_ENABLED", False)}
