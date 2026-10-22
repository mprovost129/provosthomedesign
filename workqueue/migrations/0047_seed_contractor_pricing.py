from django.db import migrations


def seed(apps,schema_editor):
    Card=apps.get_model('workqueue','PricingRateVersion')
    Card.objects.using(schema_editor.connection.alias).get_or_create(category='contractor',sequence=1,defaults={
        'label':'Contractor pricing',
        'rates':{'new':'1.25','attached':'0.75','detached':'1.25','addition':'1.25','existing':'0.75','renovation':'0.75','hourly':'125.00'},
        'source_note':'Provost Home Design Contractor Pricing.pdf; existing-condition drawings and renovation design are separate services.',
    })


class Migration(migrations.Migration):
    dependencies=[('workqueue','0046_pricing_calculations_and_client_categories')]
    # Keep historical rate versions if the migration is reversed. Estimates
    # reference their version and must not lose their pricing provenance.
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
