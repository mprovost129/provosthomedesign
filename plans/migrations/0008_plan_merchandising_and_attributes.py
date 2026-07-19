from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plans", "0007_plans_plan_name")]

    operations = [
        migrations.AddField(model_name="plans", name="ideal_for", field=models.TextField(blank=True, help_text="Who this plan suits, including lifestyle, lot, or buyer needs")),
        migrations.AddField(model_name="plans", name="key_features", field=models.TextField(blank=True, help_text="One feature per line")),
        migrations.AddField(model_name="plans", name="layout_highlights", field=models.TextField(blank=True, help_text="Room layout, circulation, and daily-living highlights")),
        migrations.AddField(model_name="plans", name="foundation_framing", field=models.TextField(blank=True, help_text="Foundation options and framing assumptions")),
        migrations.AddField(model_name="plans", name="exterior_character", field=models.TextField(blank=True, help_text="Roof form, materials, and architectural character")),
        migrations.AddField(model_name="plans", name="package_contents", field=models.TextField(blank=True, help_text="One included plan-package item per line")),
        migrations.AddField(model_name="plans", name="delivery_details", field=models.TextField(blank=True, help_text="File formats, delivery method, and typical timing")),
        migrations.AddField(model_name="plans", name="common_modifications", field=models.TextField(blank=True, help_text="One common modification per line")),
        migrations.AddField(model_name="plans", name="is_adu", field=models.BooleanField(default=False, verbose_name="ADU or carriage house")),
        migrations.AddField(model_name="plans", name="first_floor_primary", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="has_home_office", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="has_walk_in_pantry", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="has_mudroom", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="has_porch_or_deck", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="has_bonus_room", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="basement_compatible", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="narrow_lot", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="plans", name="multigenerational", field=models.BooleanField(default=False)),
    ]
