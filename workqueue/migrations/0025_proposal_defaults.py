from django.db import migrations

def seed(apps, schema_editor):
    apps.get_model('workqueue', 'ProposalSequence').objects.get_or_create(pk=1)
    Template = apps.get_model('workqueue', 'ProposalTemplate')
    for name, scope, deliverables in [
        ('New home design', 'Design services for the proposed new home. Describe the size, site, and agreed scope here.', 'List the drawings and documents included in this proposal.'),
        ('Addition / renovation', 'Design services for the proposed addition or renovation. Describe existing conditions and the areas included.', 'List the drawings, coordination, and documents included.'),
        ('Framing design', 'Framing design services for the specified project. Describe the structural areas and coordination included.', 'List the framing drawings and documents included.'),
        ('Plan revisions', 'Revisions to the existing plans. Describe the requested changes and the plans being revised.', 'List the revised drawings and documents to be provided.'),
    ]:
        Template.objects.create(name=name, scope=scope, deliverables=deliverables)

class Migration(migrations.Migration):
    dependencies = [('workqueue', '0024_proposaltemplate_projectevent_projecttask_proposal_and_more')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
