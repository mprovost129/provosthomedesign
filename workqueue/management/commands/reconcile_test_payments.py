from django.core.management.base import BaseCommand, CommandError
from workqueue.test_payment_gateway import configuration
from workqueue.test_payments import process_pending


class Command(BaseCommand):
    help = 'Recheck known Stripe sandbox payments once. Never opens checkouts or processes real payments.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 100:raise CommandError('The payment limit must be from 1 to 100.')
        if not configuration()['enabled']:
            self.stdout.write('Stripe sandbox is disabled; no payment checks ran.');return
        if not configuration()['ready']:raise CommandError('Stripe sandbox configuration needs attention.')
        checked, failed=process_pending(limit=options['limit'])
        self.stdout.write(f'Checked {checked} Stripe sandbox payment(s); {failed} need another confirmation attempt.')
        if failed:raise CommandError('Some Stripe sandbox payments could not be confirmed. Existing reservations remain protected.')
