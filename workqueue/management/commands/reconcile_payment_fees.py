from django.core.management.base import BaseCommand,CommandError
from workqueue.payment_finance import process_pending
class Command(BaseCommand):
    help='Read actual sandbox payment costs; never creates money movements.'
    def add_arguments(self,parser):parser.add_argument('--limit',type=int,default=2)
    def handle(self,*args,**options):
        if not 1<=options['limit']<=10:raise CommandError('Choose a limit from 1 to 10.')
        result=process_pending(options['limit'])
        self.stdout.write(f"Sandbox fee checks: {result['checked']}; pending or review: {result['attention']}.")
