from django.db import migrations


def seed_queue_state(apps, schema_editor):
    apps.get_model("workqueue", "QueueState").objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("workqueue", "0001_initial")]
    operations = [migrations.RunPython(seed_queue_state, migrations.RunPython.noop)]
