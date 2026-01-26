# Generated migration - this version existed on server before cleanup
# Contains profile picture fields for Client and Employee

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0011_invoice_project_alter_project_job_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='profile_picture',
            field=models.ImageField(blank=True, help_text='Optional profile picture', null=True, upload_to='profile_pictures/'),
        ),
        migrations.AddField(
            model_name='employee',
            name='profile_picture',
            field=models.ImageField(blank=True, help_text='Optional profile picture', null=True, upload_to='profile_pictures/'),
        ),
    ]
