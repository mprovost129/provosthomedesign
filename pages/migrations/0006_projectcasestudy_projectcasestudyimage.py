import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pages", "0005_alter_affiliateproduct_image_url")]

    operations = [
        migrations.CreateModel(
            name="ProjectCaseStudy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("slug", models.SlugField(blank=True, max_length=180, unique=True)),
                ("project_type", models.CharField(choices=[("custom-home", "Custom Home"), ("addition", "Addition"), ("renovation", "Renovation"), ("adu", "ADU / Carriage House"), ("plan-modification", "Plan Modification"), ("framing", "Framing / Structural Coordination"), ("other", "Other Residential Project")], db_index=True, max_length=30)),
                ("location", models.CharField(blank=True, help_text="Town and state only when the client permits disclosure.", max_length=120)),
                ("summary", models.CharField(max_length=320)),
                ("client_objective", models.TextField()),
                ("design_challenge", models.TextField()),
                ("solution", models.TextField()),
                ("deliverables", models.TextField(blank=True, help_text="One deliverable per line.")),
                ("outcome", models.TextField()),
                ("client_quote", models.TextField(blank=True)),
                ("hero_image", models.ImageField(blank=True, null=True, upload_to="projects/hero/")),
                ("meta_description", models.CharField(blank=True, max_length=180)),
                ("completed_date", models.DateField(blank=True, null=True)),
                ("is_published", models.BooleanField(db_index=True, default=False)),
                ("is_featured", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "project case study",
                "verbose_name_plural": "project case studies",
                "ordering": ("-is_featured", "-completed_date", "-created_at"),
                "indexes": [models.Index(fields=["is_published", "is_featured", "completed_date"], name="pages_proje_is_publ_1d13ac_idx")],
            },
        ),
        migrations.CreateModel(
            name="ProjectCaseStudyImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to="projects/gallery/")),
                ("image_type", models.CharField(choices=[("completed", "Completed Project"), ("construction", "Construction Progress"), ("drawing", "Drawing / Plan Detail"), ("existing", "Existing Condition"), ("rendering", "Rendering")], default="completed", max_length=20)),
                ("caption", models.CharField(blank=True, max_length=200)),
                ("alt_text", models.CharField(help_text="Describe what is visibly useful in this image.", max_length=200)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("case_study", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="images", to="pages.projectcasestudy")),
            ],
            options={
                "verbose_name": "case study image",
                "verbose_name_plural": "case study images",
                "ordering": ("order", "id"),
            },
        ),
    ]
