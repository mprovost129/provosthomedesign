from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from .crm import require_staff
from .models import WorkProject
from .document_models import InvoiceDocument, InvoiceDocumentRevision
from .document_content import checked_rich, issuer, client_projection, project_projection
from .pm_services import checked_draft, client_for_project, event
from .services import EditConflict, lock_queue


@transaction.atomic
def save_invoice_document(*, project_id, client_id, data, token, version, actor, document_id=None):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    document = InvoiceDocument.objects.filter(pk=document_id or token).first()
    if document and not document_id:
        if document.created_by_id != actor.pk or document.project_id != project_id:
            raise ValidationError('This draft belongs to another project.')
        return document
    if document_id and not document:
        raise ValidationError('This draft no longer exists.')
    if document and (document.version != version or document.project_id != project_id):
        raise EditConflict('This invoice draft changed. Reload before saving again.')
    project = WorkProject.objects.get(pk=project_id)
    client = client_for_project(project, client_id, document)
    source = document.source_revision if document else None
    if not document and data.get('source_revision'):
        from .pm_models import ProposalRevision
        source = ProposalRevision.objects.select_related('proposal__client').filter(pk=data['source_revision'],proposal__project=project,state='accepted').first()
        from .pm_services import current_client
        if not source or current_client(source.proposal.client).pk != client.pk:
            raise ValidationError('Choose an accepted agreement for this client and project.')
    if source:
        from .pm_services import current_client
        if current_client(source.proposal.client).pk != client.pk:
            raise ValidationError('The client must match the source agreement.')
    if document and str(data.get('source_revision') or '') != str(document.source_revision_id or ''):
        raise ValidationError('The source agreement cannot be changed on a saved invoice draft.')
    try:
        prepared=date.fromisoformat(data['issue_date']);due=date.fromisoformat(data['due_date'])
    except (KeyError,TypeError,ValueError):
        raise ValidationError('Provide a valid date prepared and proposed due date.') from None
    if due < prepared:
        raise ValidationError('The proposed due date cannot be before the date prepared.')
    # Reuse the same authoritative money validation without treating this
    # working paper as an accepted proposal or an issued invoice.
    checked = checked_draft({**data, 'document_kind': 'estimate', 'valid_until':due.isoformat(), **{key:'' for key in ('scope','deliverables','revisions','exclusions','timeline')}})
    checked.update(document_kind='invoice', document_style='phd-studio-v1', issuer=issuer(),
        client_name=client.name, billing_address=client_projection(client)['address'],
        project_name=project.name, project_reference=project.canonical_reference or '',
        project_address=project_projection(project)['address'])
    checked['rich_sections'] = checked_rich(checked)
    if not document:
        document = InvoiceDocument(id=token, project=project, client=client, source_revision=source, created_by=actor, version=0)
    document.client, document.title, document.draft = client, checked['title'], checked
    document.version += 1
    document.full_clean()
    document.save()
    checked = dict(checked, number=document.number, currency='USD')
    InvoiceDocumentRevision.objects.create(document=document, sequence=document.version, snapshot=checked, actor=actor)
    event(project, actor, 'Invoice working draft saved', reference=document.number, revision=document.version)
    return document
