from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from workqueue.notifications import deliver_pending


class Command(BaseCommand):
    help = "Deliver pending intake/sign-in emails; hold ambiguous outcomes for review."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if not getattr(settings, "WORK_INTAKE_ENABLED", False):
            raise CommandError("Public intake is disabled; queue email delivery is not enabled.")
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("Use a limit from 1 to 1000.")
        self.stdout.write(f"Processed {deliver_pending(options['limit'])} pending email deliveries.")
