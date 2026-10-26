from django.core.management.base import BaseCommand,CommandError
from workqueue.payout_reconciliation import process_pending
class Command(BaseCommand):
    help='Read and reconcile sandbox payouts; never initiates a transfer.'
    def add_arguments(self,parser):parser.add_argument('--limit',type=int,default=2)
    def handle(self,*args,**options):
        if not 1<=options['limit']<=10:raise CommandError('Choose a limit from 1 to 10.')
        result=process_pending(options['limit'])
        self.stdout.write(f"Sandbox payout checks: {result['checked']}; discovered: {result['discovered']}; pending or review: {result['attention']}.")
