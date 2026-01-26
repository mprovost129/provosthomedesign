from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0011_invoice_project_alter_project_job_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='profile_picture',
            field=models.ImageField(upload_to='client_pictures/', null=True, blank=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='profile_picture',
            field=models.ImageField(upload_to='employee_pictures/', null=True, blank=True),
        ),
    ]
