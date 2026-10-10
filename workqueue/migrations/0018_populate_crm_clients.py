from django.db import migrations
from workqueue.crm_identity import backfill


class Migration(migrations.Migration):
    dependencies = [("workqueue", "0017_client_clientcontact_workitem_client_contact_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
