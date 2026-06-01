from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="partnerapikey",
            name="key",
            field=models.CharField(
                blank=True,
                help_text="Optional. Leave blank to auto-generate on save.",
                max_length=64,
                unique=True,
            ),
        ),
    ]
