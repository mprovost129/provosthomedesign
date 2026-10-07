from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone
from workqueue.booking import sync_appointment
from workqueue.calendar_provider import calendar_provider
from workqueue.models import Appointment


class Command(BaseCommand):
    help = "Reconcile appointment saves, cancellations and calendar outcomes safely."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        if not getattr(settings, "BOOKING_ENABLED", False):
            raise CommandError("Appointment booking is disabled.")
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("Use a limit from 1 to 1000.")
        now = timezone.now()
        candidates = Appointment.objects.exclude(state__in=["canceled", "completed"]).filter(
            Q(last_sync_at__isnull=True) | Q(last_sync_at__lt=now-timedelta(minutes=5))).exclude(
            state="syncing", sync_started_at__gt=now-timedelta(minutes=15)).order_by("-cancel_requested", F("last_sync_at").asc(nulls_first=True), "created_at")
        provider = calendar_provider()
        processed = sum(sync_appointment(pk, provider) for pk in list(candidates.values_list("pk", flat=True)[:options["limit"]]))
        self.stdout.write(f"Reconciled {processed} appointments; uncertain outcomes remain reserved for review.")
