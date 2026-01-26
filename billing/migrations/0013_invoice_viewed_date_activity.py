# Generated migration - this version existed on server before cleanup
# Adds invoice.viewed_date and Activity model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0012_client_profile_picture_employee_profile_picture'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='viewed_date',
            field=models.DateTimeField(blank=True, help_text='When client first viewed invoice', null=True),
        ),
        migrations.CreateModel(
            name='Activity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('activity_type', models.CharField(choices=[('note', 'Note'), ('call', 'Phone Call'), ('email', 'Email'), ('meeting', 'Meeting'), ('site_visit', 'Site Visit'), ('proposal_sent', 'Proposal Sent'), ('proposal_viewed', 'Proposal Viewed'), ('proposal_accepted', 'Proposal Accepted'), ('proposal_rejected', 'Proposal Rejected'), ('invoice_sent', 'Invoice Sent'), ('invoice_viewed', 'Invoice Viewed'), ('payment_received', 'Payment Received'), ('project_started', 'Project Started'), ('project_completed', 'Project Completed'), ('status_change', 'Status Change'), ('other', 'Other')], default='note', max_length=30)),
                ('title', models.CharField(help_text='Brief activity title', max_length=200)),
                ('description', models.TextField(blank=True, help_text='Detailed notes or description')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('is_pinned', models.BooleanField(default=False, help_text='Pin important activities to top')),
                ('is_internal', models.BooleanField(default=False, help_text='Internal note (not visible to client)')),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activities', to='billing.client')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Activity',
                'verbose_name_plural': 'Activities',
                'ordering': ['-created_at'],
            },
        ),
    ]
