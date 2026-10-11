from django.core.management.base import BaseCommand
from workqueue.freshbooks import sync_billing


class Command(BaseCommand):
    help = 'Prepare explicitly requested FreshBooks invoice drafts and refresh linked balances.'
    def handle(self, *args, **options):
        self.stdout.write(f'FreshBooks billing records processed: {sync_billing()}')
