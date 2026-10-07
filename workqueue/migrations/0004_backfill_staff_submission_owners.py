from django.db import migrations


def backfill_staff_owners(apps, schema_editor):
    Submission = apps.get_model("workqueue", "Submission")
    # Preserve trusted manual capture ownership from the foundation stage.
    # Public submitted claims are not treated as verified project access grants.
    for record in Submission.objects.using(schema_editor.connection.alias).filter(channel="staff", owner_email="").select_related("work_item").iterator():
        Submission.objects.using(schema_editor.connection.alias).filter(pk=record.pk).update(
            owner_email=record.work_item.contact_email.strip().lower())


class Migration(migrations.Migration):
    dependencies = [("workqueue", "0003_emailaccesstoken_notificationdelivery_body_and_more")]
    operations = [migrations.RunPython(backfill_staff_owners, migrations.RunPython.noop)]
