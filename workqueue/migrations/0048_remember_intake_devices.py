import uuid
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [('workqueue', '0047_seed_contractor_pricing')]
    operations = [
        migrations.CreateModel(name='IntakeDevice', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('email', models.EmailField(max_length=254)),
            ('digest', models.CharField(max_length=64, unique=True)),
            ('created_at', models.DateTimeField(default=timezone.now)),
            ('expires_at', models.DateTimeField(db_index=True)),
            ('revoked_at', models.DateTimeField(blank=True, null=True)),
        ]),
        migrations.AlterField(model_name='emailaccesstoken', name='destination',
            field=models.CharField(choices=[('tracking', 'Tracking'), ('booking', 'Booking'), ('portal', 'Client portal'),
                ('consult', 'Consultation'), ('intake', 'Intake details')], default='tracking', max_length=10)),
    ]
