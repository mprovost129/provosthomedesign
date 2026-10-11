from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workqueue', '0019_contact_archive')]
    operations = [migrations.AddField(
        model_name='workitem', name='deleted_at',
        field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
    )]
