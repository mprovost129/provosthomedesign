from django.db import migrations, models


def restore_legacy_state(apps, schema_editor):
    # Permit rollback of the constraint without deleting verified inquiries.
    apps.get_model('workqueue', 'ConsultationRequest').objects.using(
        schema_editor.connection.alias).filter(state='ready').update(state='approved')


class Migration(migrations.Migration):
    dependencies = [('workqueue', '0039_consultation_requests')]

    operations = [
        migrations.RemoveConstraint(model_name='consultationrequest', name='wq_consultation_state'),
        migrations.AlterField(
            model_name='consultationrequest', name='state',
            field=models.CharField(max_length=12, default='unverified', choices=[
                ('unverified', 'Email verification needed'), ('ready', 'Ready to book'),
                ('pending', 'Awaiting approval'), ('approved', 'Approved'), ('declined', 'Declined'),
            ]),
        ),
        migrations.AddConstraint(
            model_name='consultationrequest',
            constraint=models.CheckConstraint(condition=models.Q(state__in=[
                'unverified', 'ready', 'pending', 'approved', 'declined',
            ]), name='wq_consultation_state'),
        ),
        migrations.RunPython(migrations.RunPython.noop, restore_legacy_state),
    ]
