"""One finite scheduled run for calendar, email and expired-upload cleanup."""
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Run enabled queue background tasks once; disabled features do no work."

    def add_arguments(self, parser):
        parser.add_argument("--appointment-limit", type=int, default=50)
        parser.add_argument("--email-limit", type=int, default=100)

    def handle(self, *args, **options):
        for name in ("appointment_limit", "email_limit"):
            if not 1 <= options[name] <= 1000:
                raise CommandError("Task limits must be from 1 to 1000.")
        tasks = []
        intake = getattr(settings, "WORK_INTAKE_ENABLED", False)
        if intake and getattr(settings, "BOOKING_ENABLED", False):
            tasks.append(("sync_queue_appointments", {"limit": options["appointment_limit"]}))
        if intake:
            tasks.extend([
                ("process_queue_emails", {"limit": options["email_limit"]}),
                ("cleanup_queue_uploads", {}),
            ])
        if not tasks:
            self.stdout.write("Queue features are disabled; no background tasks ran.")
            return
        failed = []
        for name, arguments in tasks:
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr, **arguments)
            except Exception:
                # Do not leak database credentials or provider responses in job logs.
                # Other tasks still run; the final nonzero exit makes failure visible.
                failed.append(name)
                self.stderr.write(f"Background task failed: {name}.")
            finally:
                close_old_connections()
        if failed:
            raise CommandError("Queue tasks need attention: " + ", ".join(failed)) from None
