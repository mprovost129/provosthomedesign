import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plans", "0010_plans_is_popular")]

    operations = [
        migrations.CreateModel(
            name="SavedPlanEmailReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("consented_at", models.DateTimeField(auto_now_add=True)),
                ("next_send_at", models.DateTimeField(db_index=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("plans", models.ManyToManyField(related_name="email_reminders", to="plans.plans")),
            ],
            options={"ordering": ("next_send_at",)},
        ),
        migrations.AddIndex(
            model_name="savedplanemailreminder",
            index=models.Index(fields=["is_active", "next_send_at"], name="plans_saved_is_acti_042709_idx"),
        ),
    ]
