from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plans", "0006_remove_plans_plans_plans_house_s_82aeb9_idx")]

    operations = [
        migrations.AddField(
            model_name="plans",
            name="plan_name",
            field=models.CharField(
                blank=True,
                help_text="Descriptive public name, such as 'The Rehoboth Ranch'",
                max_length=120,
            ),
        ),
    ]
