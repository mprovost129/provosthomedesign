# Generated migration - ProposalTemplate, Proposal, and ProposalLineItem models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0013_invoice_viewed_date_activity'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProposalTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Template name', max_length=255)),
                ('description', models.TextField(blank=True, help_text='Optional description')),
                ('content', models.TextField(help_text='HTML content template')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='Proposal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('proposal_number', models.CharField(help_text='Unique proposal identifier', max_length=100, unique=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('sent', 'Sent'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('expired', 'Expired')], default='draft', max_length=20)),
                ('issue_date', models.DateField(help_text='When proposal was issued')),
                ('expiry_date', models.DateField(blank=True, help_text='When proposal expires', null=True)),
                ('description', models.TextField(blank=True, help_text='Proposal description')),
                ('total_amount', models.DecimalField(decimal_places=2, help_text='Total proposal amount', max_digits=12)),
                ('notes', models.TextField(blank=True, help_text='Internal notes')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='proposals', to='billing.client')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ProposalLineItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.CharField(help_text='Item description', max_length=255)),
                ('quantity', models.DecimalField(decimal_places=2, default=1, max_digits=10)),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=12)),
                ('proposal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='line_items', to='billing.proposal')),
            ],
        ),
    ]
