from django.conf import settings
from django.core.management.base import BaseCommand
from workqueue.portal_export_worker import process_exports

class Command(BaseCommand):
    help = 'Process at most one requested marked-plan PDF export.'
    def handle(self,*args,**options):
        count = process_exports(limit=1) if getattr(settings,'WORK_INTAKE_ENABLED',False) else 0
        self.stdout.write(f'Processed {count} requested plan exports.')
