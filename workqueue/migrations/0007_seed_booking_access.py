from django.db import migrations


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model("workqueue", "BookingState").objects.using(alias).get_or_create(pk=1)
    Access = apps.get_model("workqueue", "BookingAccess")
    Claim = apps.get_model("workqueue", "ProjectClaim")
    for email in Claim.objects.using(alias).exclude(verified_at__isnull=True).values_list("email", flat=True).distinct():
        Access.objects.using(alias).get_or_create(email=email.strip().lower())


class Migration(migrations.Migration):
    dependencies = [("workqueue", "0006_booking_schema")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
