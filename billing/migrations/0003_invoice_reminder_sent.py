from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_invoicetemplate_invoice_email_sent_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='reminder_sent',
            field=models.BooleanField(default=False, help_text='Whether overdue reminder was sent'),
        ),
    ]
