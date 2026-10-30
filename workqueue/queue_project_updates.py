"""Private project defaults and explicit staff updates; no access grants or mail."""
from django.core.exceptions import ValidationError
from django.db import transaction
from .crm import require_staff
from .models import Client, Submission, WorkItem, WorkProject
from .services import create_work, lock_queue

PROJECT_FIELDS = {'project_name':'name','project_street':'street','project_city':'city',
                  'project_state':'state','project_zip':'zip_code'}
CONTACT_FIELDS = ['contact_full_name','company','contact_email','contact_phone']
BILLING_FIELDS = ['billing_street','billing_city','billing_state','billing_zip']
PREFILL_FIELDS = [*PROJECT_FIELDS,*CONTACT_FIELDS,*BILLING_FIELDS,'service_needed','description',
                  'project_context','requested_deadline']


def project_label(project):
    folder = getattr(project,'filing_folder',None)
    references = [value for value in [folder.number if folder else None,project.canonical_reference] if value]
    address = ', '.join(value for value in [project.street,project.city,project.state,project.zip_code] if value)
    return ' — '.join(value for value in [' / '.join(references),project.name,address] if value)


def project_defaults(project, source_id=None):
    """Caller must already have staff workspace access. Never read deleted work."""
    sources = WorkItem.objects.filter(project=project).select_related('client_contact__client')
    source = sources.filter(pk=source_id).first() if source_id else sources.order_by('-received_at','-id').first()
    if source_id and source is None:
        raise ValidationError('The selected source request is no longer available on this project. Load the project details again.')
    contact = source.client_contact if source and source.client_contact_id else None
    if contact and (contact.archived_at or contact.client.merged_into_id):
        contact = None
    fields = {name:getattr(project,attribute) for name,attribute in PROJECT_FIELDS.items()}
    fields.update({name:getattr(source,name) if source else '' for name in [*CONTACT_FIELDS,*BILLING_FIELDS,'service_needed','description']})
    if contact:
        client = contact.client
        fields.update(contact_full_name=contact.full_name or client.name,
            company=client.name if client.kind==Client.Kind.COMPANY else 'Homeowner',
            contact_email=contact.email or '',contact_phone=contact.phone)
        fields.update({name:getattr(client,name) for name in BILLING_FIELDS})
    # Deadlines and queue/scheduling state belong to this new request.
    fields.update(project_context=project.canonical_reference or '',requested_deadline='')
    return {'project':project,'source':source,'contact':contact,'fields':fields}


def initial_for_update(project, source_id=None):
    defaults = project_defaults(project,source_id)
    return {**defaults['fields'],'project':project.pk,'kind':WorkItem.Kind.UPDATE,
            'update_source':defaults['source'].pk if defaults['source'] else None}


@transaction.atomic
def create_manual_work(*, data, actor, idempotency_key, source_id=None):
    require_staff(actor,'workqueue.add_workitem');lock_queue()
    existing = Submission.objects.select_related('work_item').filter(idempotency_key=idempotency_key).first()
    if existing:return existing.work_item,False
    data = dict(data);project = data.get('project')
    if not project:
        if source_id:raise ValidationError('Choose the project for the loaded update, or return to a new manual entry.')
        return create_work(data=data,actor=actor,idempotency_key=idempotency_key)
    project = WorkProject.objects.select_related('filing_folder').get(pk=project.pk)
    defaults = project_defaults(project,source_id)
    data.update(project=project,kind=WorkItem.Kind.UPDATE,previous_request=defaults['source'])
    # The explicit selected source retains its CRM company association even if
    # another company shares the email. Entered values are request snapshots;
    # they never edit the old job, shared project or CRM profile.
    return create_work(data=data,actor=actor,idempotency_key=idempotency_key,staff_contact=defaults['contact'])
