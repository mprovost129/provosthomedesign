from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pages", "0006_projectcasestudy_projectcasestudyimage")]

    operations = [
        migrations.AddField(model_name="projectinquiry", name="project_type", field=models.CharField(blank=True, choices=[("new-home", "New custom home"), ("stock-plan", "Stock plan purchase"), ("addition", "Addition or renovation"), ("plan-modification", "Stock plan modification"), ("framing", "Framing plans"), ("adu", "ADU or accessory building"), ("not-sure", "Not sure yet")], max_length=30)),
        migrations.AddField(model_name="projectinquiry", name="project_location", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="projectinquiry", name="approximate_size", field=models.CharField(blank=True, choices=[("under-1000", "Under 1,000 sq ft"), ("1000-1999", "1,000-1,999 sq ft"), ("2000-2999", "2,000-2,999 sq ft"), ("3000-plus", "3,000+ sq ft"), ("not-sure", "Not sure yet")], max_length=20)),
        migrations.AddField(model_name="projectinquiry", name="project_timeline", field=models.CharField(blank=True, choices=[("asap", "As soon as practical"), ("3-6-months", "Within 3-6 months"), ("6-12-months", "Within 6-12 months"), ("12-plus-months", "More than 12 months"), ("researching", "Researching options")], max_length=20)),
        migrations.AddField(model_name="projectinquiry", name="budget_range", field=models.CharField(blank=True, choices=[("under-250k", "Under $250,000"), ("250k-500k", "$250,000-$500,000"), ("500k-750k", "$500,000-$750,000"), ("750k-plus", "$750,000+"), ("not-sure", "Not sure yet")], max_length=20)),
        migrations.AddField(model_name="projectinquiry", name="consultation_requested", field=models.BooleanField(default=False)),
    ]
