from django.core.management.base import BaseCommand
from workqueue.freshbooks_projects import process_one


class Command(BaseCommand):
    help='Process one explicitly approved FreshBooks project step; never sends invoices.'
    def handle(self,*args,**options):
        done=process_one()
        self.stdout.write('Processed one FreshBooks project step.' if done else 'No FreshBooks project step was ready.')
