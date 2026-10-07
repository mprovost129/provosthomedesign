from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from workqueue.calendar_provider import CalendarUnavailable, GoogleCalendar


class Command(BaseCommand):
    help = "Read-only Google Calendar identity/availability check; creates no events or emails."

    def handle(self, *args, **options):
        try:
            provider = GoogleCalendar()
            metadata = provider.identity(settings.GOOGLE_CALENDAR_ID)
            provider.busy(timezone.now(), timezone.now()+timedelta(days=7), calendar_id=metadata["id"])
        except CalendarUnavailable as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write("Google Calendar identity and availability verified. No events were changed.")
