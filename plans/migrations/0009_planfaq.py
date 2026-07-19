import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plans", "0008_plan_merchandising_and_attributes")]

    operations = [
        migrations.CreateModel(
            name="PlanFAQ",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("question", models.CharField(max_length=240)),
                ("answer", models.TextField()),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="faqs", to="plans.plans")),
            ],
            options={
                "verbose_name": "plan FAQ",
                "verbose_name_plural": "plan FAQs",
                "ordering": ("order", "id"),
            },
        ),
    ]
