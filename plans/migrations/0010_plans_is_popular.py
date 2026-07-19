from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plans", "0009_planfaq")]

    operations = [
        migrations.AddField(
            model_name="plans",
            name="is_popular",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Display a Popular label on public plan cards and detail pages.",
            ),
        ),
    ]
