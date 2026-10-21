from django.core.management.base import BaseCommand
from workqueue.freshbooks import sync_billing
from workqueue.invoice_history_sync import sync_history
from workqueue.dashboard_billing import sync_summaries


class Command(BaseCommand):
    help = 'Prepare explicitly requested FreshBooks invoice drafts and refresh linked balances.'
    def handle(self, *args, **options):
        self.stdout.write(f'FreshBooks billing records processed: {sync_billing()}')
        self.stdout.write(f'Imported invoice check steps processed: {sync_history()}')
        self.stdout.write(f'Dashboard summary steps processed: {sync_summaries()}')
