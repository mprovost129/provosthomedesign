# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('plans', '0002_plancomparison_savedplan'),
    ]

    operations = [
        # Step 1: Add the new many-to-many field
        migrations.AddField(
            model_name='plans',
            name='house_styles',
            field=models.ManyToManyField(blank=True, related_name='plans', to='plans.housestyle'),
        ),
    ]
