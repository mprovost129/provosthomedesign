from django.core.management.base import BaseCommand
from django.utils import timezone

from plans.models import SavedPlanEmailReminder
from plans.reminders import send_saved_plan_email


class Command(BaseCommand):
    help = "Send due one-time saved-plan reminders and deactivate them."

    def handle(self, *args, **options):
        reminders = SavedPlanEmailReminder.objects.filter(
            is_active=True,
            next_send_at__lte=timezone.now(),
        ).prefetch_related("plans", "plans__house_styles")
        sent = 0
        for reminder in reminders:
            try:
                delivered = send_saved_plan_email(reminder, follow_up=True)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"Could not send reminder {reminder.pk}: {exc}"))
                continue
            reminder.is_active = False
            if delivered:
                reminder.sent_at = timezone.now()
                sent += 1
            reminder.save(update_fields=["is_active", "sent_at"])
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} saved-plan reminder(s)."))
