from django.core.management.base import BaseCommand
from workqueue.dropbox_filing import process_one


class Command(BaseCommand):
    help = 'Copy one approved document to Dropbox, preserving all original uploads.'

    def handle(self,*args,**options):
        result=process_one()
        self.stdout.write('Processed one approved Dropbox filing step.' if result else 'No Dropbox filing step was ready.')
