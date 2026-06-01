# Generated manually

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('plans', '0004_copy_house_style_data'),
    ]

    operations = [
        # Remove the old foreign key field
        migrations.RemoveField(
            model_name='plans',
            name='house_style',
        ),
    ]
