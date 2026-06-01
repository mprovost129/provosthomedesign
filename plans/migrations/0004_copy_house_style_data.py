# Generated manually

from django.db import migrations


def copy_house_style_to_styles(apps, schema_editor):
    """Copy existing house_style (ForeignKey) to house_styles (M2M)"""
    Plans = apps.get_model('plans', 'Plans')
    for plan in Plans.objects.all():
        if plan.house_style_id:
            plan.house_styles.add(plan.house_style_id)


class Migration(migrations.Migration):

    dependencies = [
        ('plans', '0003_add_house_styles_m2m'),
    ]

    operations = [
        migrations.RunPython(copy_house_style_to_styles, migrations.RunPython.noop),
    ]
