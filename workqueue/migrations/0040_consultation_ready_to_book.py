from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workqueue', '0039_consultation_requests')]

    # Change only the label. Both app revisions read the same state and use
    # the same uniqueness constraint during deployment and rollback.
    operations = [
        migrations.AlterField(
            model_name='consultationrequest', name='state',
            field=models.CharField(max_length=12, default='unverified', choices=[
                ('unverified', 'Email verification needed'), ('pending', 'Ready to book'),
                ('approved', 'Approved'), ('declined', 'Declined'),
            ]),
        ),
    ]
