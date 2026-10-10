from django.core.management.base import BaseCommand
from workqueue.crm import sync_clients


class Command(BaseCommand):
    help = "Connect existing unlinked work requests to internal CRM client records. Idempotent; grants no access."

    def handle(self, *args, **options):
        self.stdout.write(f"Connected {sync_clients()} previously unlinked work requests.")
