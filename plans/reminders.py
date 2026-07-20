from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import SavedPlanEmailReminder


def send_saved_plan_email(reminder: SavedPlanEmailReminder, *, follow_up: bool) -> int:
    plans = list(reminder.plans.filter(is_available=True).prefetch_related("house_styles"))
    if not plans:
        return 0

    base_url = (getattr(settings, "MAIN_SITE_URL", "") or getattr(settings, "SITE_URL", "")).rstrip("/")
    context = {
        "plans": plans,
        "base_url": base_url,
        "follow_up": follow_up,
        "unsubscribe_url": f"{base_url}/plans/favorites/reminders/{reminder.token}/",
    }
    subject = "A reminder about your saved house plans" if follow_up else "Your saved house plans"
    text_body = render_to_string("plans/emails/saved_plans.txt", context)
    html_body = render_to_string("plans/emails/saved_plans.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[reminder.email],
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)
