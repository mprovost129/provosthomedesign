"""One-way GET-only FreshBooks mirror, with bounded resumable scans."""
import time
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from . import freshbooks as fb
from .billing_mail import require_owner
from .crm import require_staff
from .crm_identity import normalized_text,phone_key
from .pm_services import current_client
from .models import Client,ClientContact,ClientEvent
from .freshbooks_models import FreshBooksConnection,FreshBooksClientLink,FreshBooksInvoice
from .freshbooks_project_models import FreshBooksProject
from .freshbooks_mirror_models import (KINDS,FreshBooksMirrorSettings,FreshBooksMirrorJob,FreshBooksMirrorRecord,
    FreshBooksMirrorRevision,FreshBooksMirrorEvent)
from .freshbooks_mirror_data import normalize,signature,number
from .services import lock_queue,EditConflict

SCOPES={'clients':'user:clients:read','projects':'user:projects:read','invoices':'user:invoices:read',
    'payments':'user:payments:read','estimates':'user:estimates:read'}
ERROR='The complete FreshBooks scan could not be finished. Saved records are retained. Check the connection or source record, then retry.'
MAX_PAGES=200


def selected():
    if not fb.configured():return None
    return FreshBooksConnection.objects.filter(pk=1).exclude(account_id='').first()


def config(connection):
    return FreshBooksMirrorSettings.objects.filter(pk=connection.account_id).first() or FreshBooksMirrorSettings(
        account_id=connection.account_id,business_id=connection.business_id or 0,business_name=connection.business_name)


def available(connection,kind):
    return bool(connection and not connection.needs_reconnect and connection.encrypted_tokens
        and connection.business_id and SCOPES[kind] in connection.granted_scopes)


@transaction.atomic
def save_settings(*,actor,version,enabled,include_estimates):
    require_owner(actor);require_staff(actor,'workqueue.change_workitem');lock_queue()
    connection=selected()
    if not connection or not connection.business_id:raise ValidationError('Connect FreshBooks and select its business first.')
    row=config(connection)
    if row.version!=version:raise EditConflict('Mirror settings changed. Reload before saving.')
    if row.business_id!=connection.business_id and FreshBooksMirrorRecord.objects.filter(account_id=connection.account_id).exists():
        raise ValidationError('The business identity changed for this account. Review the existing source mappings before enabling it.')
    if type(enabled) is not bool or type(include_estimates) is not bool:raise ValidationError('Review the mirror settings.')
    if enabled and any(not available(connection,kind) for kind in ('clients','projects','invoices','payments')):
        raise ValidationError('Reconnect FreshBooks with client, project, invoice and payment read access first.')
    if include_estimates and not available(connection,'estimates'):
        raise ValidationError('Read-only estimate access is required before enabling estimate mirroring.')
    row.business_id=connection.business_id;row.business_name=connection.business_name
    row.enabled=enabled;row.include_estimates=include_estimates;row.version+=1;row.changed_at=timezone.now();row.changed_by=actor;row.save()
    FreshBooksMirrorEvent.objects.create(account_id=row.account_id,actor=actor,action='Mirror settings saved',
        details={'enabled':enabled,'estimates':include_estimates,'version':row.version})
    return row


@transaction.atomic
def request_sync(actor=None,automatic=False):
    if actor:require_staff(actor,'workqueue.change_workitem')
    lock_queue();connection=selected()
    if not connection:return 0
    settings=config(connection)
    if not settings.enabled or settings.business_id!=connection.business_id or connection.needs_reconnect:
        if automatic:return 0
        raise ValidationError('Enable the mirror for the connected FreshBooks business first.')
    count=0;now=timezone.now()
    for kind,_ in KINDS:
        if kind=='estimates' and not settings.include_estimates or not available(connection,kind):continue
        job=FreshBooksMirrorJob.objects.filter(account_id=connection.account_id,kind=kind).first()
        if job and job.state in ('queued','working'):continue
        if job and (automatic and job.next_at>now or not automatic and job.requested_at>now-timedelta(minutes=1)):continue
        if not job:job=FreshBooksMirrorJob(account_id=connection.account_id,kind=kind,business_id=connection.business_id)
        job.business_id=connection.business_id;job.generation=uuid.uuid4();job.state='queued';job.page=1
        job.pages=job.expected_total=None;job.seen_ids=[];job.error=job.error_reference='';job.lease=job.lease_until=None
        job.requested_at=now;job.next_at=now;job.save();count+=1
    return count


def read_page(job):
    params={'page':job.page,'per_page':100}
    if job.kind=='projects':
        path=f'/projects/business/{job.business_id}/projects';params['skip_group']='true'
    else:
        endpoints={'clients':'users/clients','invoices':'invoices/invoices','payments':'payments/payments','estimates':'estimates/estimates'}
        path=f'/accounting/account/{job.account_id}/'+endpoints[job.kind]
        if job.kind in ('invoices','estimates'):params['include[]']='lines'
    body=fb.api('GET',path,params=params)
    result=body if job.kind=='projects' else body.get('response',{}).get('result',{})
    if not isinstance(result,dict) or not isinstance(result.get(job.kind),list):raise ValueError('Incomplete source page')
    meta=result.get('meta',{}) if job.kind=='projects' else result
    total=meta.get('total');pages=meta.get('pages');page=meta.get('page');per_page=meta.get('per_page')
    if any(type(v) is not int for v in (total,pages,page,per_page)) or total<0 or not 0<=pages<=MAX_PAGES or per_page!=100:
        raise ValueError('Invalid pagination')
    if page!=job.page and not(job.kind=='projects' and page==0 and job.page==1):raise ValueError('Wrong page')
    if pages!=max(1,(total+99)//100) and not(total==0 and pages==0):raise ValueError('Incomplete pagination')
    raw=result[job.kind]
    if len(raw)!=min(100,max(0,total-(job.page-1)*100)):raise ValueError('Incomplete source page')
    if job.pages is not None and (job.pages!=pages or job.expected_total!=total):raise ValueError('Source changed during scan')
    seen=set(job.seen_ids);rows=[]
    for item in raw:
        identifier=number(item.get('id')) if isinstance(item,dict) else None
        if not identifier or identifier in seen:raise ValueError('Duplicate source identity')
        try:row=normalize(item,job.account_id,job.kind)
        except (ValueError,TypeError,KeyError,AttributeError):
            raise SourceRecordError(str(identifier)) from None
        seen.add(identifier);rows.append(row)
    return rows,pages,total,sorted(seen)


class SourceRecordError(ValueError):
    def __init__(self,reference):self.reference=reference;super().__init__('Source record needs review')


def candidate_client(source):
    company=bool(source['organization'] and normalized_text(source['organization']) not in
        {normalized_text(source['full_name']),'homeowner'})
    kind=Client.Kind.COMPANY if company else Client.Kind.INDIVIDUAL
    identity=normalized_text(source['organization'] if company else source['full_name'] or source['title'])
    if source['email']:
        contacts=ClientContact.objects.filter(email__iexact=source['email'],archived_at__isnull=True).select_related('client')
        candidates={current_client(c.client).pk:current_client(c.client) for c in contacts}
        exact=[c for c in candidates.values() if c.kind==kind and normalized_text(c.name)==identity]
        if len(exact)==1:return exact[0],False
        if candidates:return None,True
    matches=Client.objects.filter(normalized_name=identity,kind=kind,merged_into__isnull=True)
    if matches.exists():return None,True # A matching name alone does not establish identity.
    return None,False


def client_fields(source):
    company=bool(source['organization'] and normalized_text(source['organization']) not in
        {normalized_text(source['full_name']),'homeowner'})
    address=source['address']
    return {'client.name':source['title'],'client.billing_street':'\n'.join(filter(None,[address['street'],address['street2']])),
        'client.billing_city':address['city'],'client.billing_state':address['state'],'client.billing_zip':address['zip'],
        'contact.full_name':source['full_name'],'contact.email':source['email'] or None,'contact.phone':source['phone']},company


def sync_client(record):
    source=record.source;issues=[]
    if source['invalid_email']:issues.append('FreshBooks email is invalid; no email access was added.')
    if source['visibility']!=0:
        link=FreshBooksClientLink.objects.filter(account_id=record.account_id,customer_id=record.provider_id).select_related('client').first()
        if link:record.client=current_client(link.client)
        elif not record.client_id:
            match,ambiguous=candidate_client(source)
            if match:record.client=match
            if ambiguous:issues.append('Archived source has an ambiguous CRM identity; existing profiles are preserved.')
        record.issues=issues;return
    fields,company=client_fields(source)
    limits={'client.name':200,'client.billing_street':250,'client.billing_city':100,'client.billing_state':50,
        'client.billing_zip':20,'contact.full_name':200,'contact.phone':50,'contact.email':254}
    if any(value is not None and len(value)>limits[key] for key,value in fields.items()):
        record.issues=issues+['FreshBooks contact details exceed CRM field limits; review before linking.'];return
    link=FreshBooksClientLink.objects.filter(account_id=record.account_id,customer_id=record.provider_id).select_related('client').first()
    client=current_client(link.client) if link else current_client(record.client) if record.client_id else None
    created=False
    if client is None:
        client,ambiguous=candidate_client(source)
        if ambiguous:
            record.issues=issues+['Choose the CRM client: identity is ambiguous. Shared emails do not merge companies.'];return
        if client is None:
            if not source['organization'] and not source['full_name']:
                record.issues=issues+['A client name is needed before creating a CRM profile.'];return
            client=Client.objects.create(name=source['title'],normalized_name=normalized_text(source['title']),
                kind=Client.Kind.COMPANY if company else Client.Kind.INDIVIDUAL)
            created=True
    existing_link=FreshBooksClientLink.objects.filter(account_id=record.account_id,client=client).first()
    if existing_link and existing_link.customer_id!=record.provider_id:
        record.issues=issues+['This CRM client is already linked to another FreshBooks client. Review the separate billing identities.'];return
    contact=ClientContact.objects.filter(pk=record.contact_id,client=client,archived_at__isnull=True).first() if record.contact_id else None
    if record.contact_id and contact is None and ClientContact.objects.filter(pk=record.contact_id,archived_at__isnull=False).exists():
        record.issues=issues+['The CRM contact was archived; automatic sync will not restore it.'];return
    if contact is None and source['email']:
        contact=ClientContact.objects.filter(client=client,email__iexact=source['email'],archived_at__isnull=True).first()
    new_contact=contact is None
    if new_contact:
        contact=ClientContact.objects.create(client=client,email=source['email'] or None,full_name=source['full_name'],
            phone=source['phone'],submitted_company=source['organization'] or 'Homeowner',booking_requires_approval=True,
            normalized_name=normalized_text(source['full_name']),normalized_phone=phone_key(source['phone']),
            normalized_company=normalized_text(source['organization']))
    applied=dict(record.applied_fields);changed=False
    for key,value in fields.items():
        target,field=key.split('.');obj=client if target=='client' else contact
        old=getattr(obj,field);baseline=applied.get(key,object())
        if target=='contact' and contact.linked_contact_id and old!=value:
            issues.append('Shared CRM contact details were retained; review the linked contact before changing them.');continue
        same_text=normalized_text(old or '')==normalized_text(value or '')
        permitted=created if target=='client' else new_contact
        if permitted or old in ('',None) or old==baseline or same_text:
            if field=='email' and value and ClientContact.objects.filter(client=client,email__iexact=value).exclude(pk=contact.pk).exists():
                issues.append('CRM email conflicts with another contact; existing email retained.');continue
            if old!=value:setattr(obj,field,value);changed=True
            applied[key]=value
        elif old!=value:
            issues.append('CRM '+field.replace('_',' ')+' was edited separately; existing value retained.')
    client.normalized_name=normalized_text(client.name)
    contact.normalized_name=normalized_text(contact.full_name);contact.normalized_phone=phone_key(contact.phone)
    if not client.primary_contact_id:client.primary_contact=contact;changed=True
    if changed or created or new_contact:
        client.version+=1;client.save();contact.save()
        ClientEvent.objects.create(client=client,action='FreshBooks details copied',changes={'source_id':record.provider_id,'account':record.account_id})
    FreshBooksClientLink.objects.update_or_create(account_id=record.account_id,customer_id=record.provider_id,
        defaults={'client':client,'label':source['title'][:250],'email':source['email']})
    record.client=client;record.contact=contact;record.applied_fields=applied;record.issues=sorted(set(issues))
    FreshBooksMirrorRecord.objects.filter(account_id=record.account_id,customer_id=record.provider_id).exclude(kind='clients').update(client=client)


def relationships(record):
    if record.kind=='clients':sync_client(record);return
    record.issues=[];record.project=None
    parent=FreshBooksMirrorRecord.objects.filter(account_id=record.account_id,kind='clients',provider_id=record.customer_id).select_related('client').first() if record.customer_id else None
    record.client=current_client(parent.client) if parent and parent.client_id else None
    if record.customer_id and not record.client_id:record.issues.append('Billing client is not yet linked to the CRM.')
    if record.kind=='projects':
        link=FreshBooksProject.objects.filter(account_id=record.account_id,business_id=record.business_id,remote_id=record.provider_id).first()
        if link and link.customer_id==record.customer_id:record.project_id=link.project_id
    elif record.kind=='invoices':
        link=FreshBooksInvoice.objects.filter(account_id=record.account_id,invoice_id=record.provider_id,customer_id=record.customer_id).select_related('proposal').first()
        if link:record.project_id=link.project_id or (link.proposal.project_id if link.proposal_id else None)
        if not record.project_id:
            ids={line['project_id'] for line in record.source['lines'] if line['project_id']}
            links=FreshBooksProject.objects.filter(account_id=record.account_id,business_id=record.business_id,remote_id__in=ids,customer_id=record.customer_id)
            projects=set(links.values_list('project_id',flat=True))
            if len(projects)==1:record.project_id=next(iter(projects))
    elif record.kind=='payments' and record.provider_invoice_id:
        invoice=FreshBooksMirrorRecord.objects.filter(account_id=record.account_id,kind='invoices',provider_id=record.provider_invoice_id).first()
        if invoice and record.customer_id in (None,invoice.customer_id):
            record.project_id=invoice.project_id;record.client_id=invoice.client_id;record.customer_id=invoice.customer_id
        elif invoice:record.issues.append('Payment client does not agree with its FreshBooks invoice; billing identity needs review.')


def apply_row(job,source):
    record=FreshBooksMirrorRecord.objects.filter(account_id=job.account_id,kind=job.kind,provider_id=source['provider_id']).first()
    stamp=signature(source);changed=not record or record.source_hash!=stamp
    if not record:
        record=FreshBooksMirrorRecord(account_id=job.account_id,business_id=job.business_id,kind=job.kind,provider_id=source['provider_id'],
            source=source,source_hash=stamp,title=source['title'],last_seen_generation=job.generation)
    elif changed:record.revision+=1;record.version+=1
    record.source=source;record.source_hash=stamp;record.title=source['title'];record.customer_id=source.get('customer_id');record.business_id=job.business_id
    record.provider_invoice_id=source.get('provider_invoice_id');record.last_seen_generation=job.generation
    record.missing=False;record.verified_at=timezone.now()
    relationships(record);record.save()
    if changed:FreshBooksMirrorRevision.objects.create(record=record,sequence=record.revision,source=source,source_hash=stamp)
    return record


def process_step():
    connection=selected()
    if not connection:return False
    settings=config(connection)
    if not settings.enabled or settings.business_id!=connection.business_id or connection.needs_reconnect:return False
    with transaction.atomic():
        lock_queue()
        kinds=[kind for kind,_ in KINDS if available(connection,kind) and (kind!='estimates' or settings.include_estimates)]
        job=FreshBooksMirrorJob.objects.filter(account_id=connection.account_id,business_id=connection.business_id,kind__in=kinds,state__in=['queued','working']).filter(
            Q(lease_until__isnull=True)|Q(lease_until__lt=timezone.now())).order_by('requested_at','kind').first()
        if not job:return False
        if not available(connection,job.kind) or job.kind=='estimates' and not settings.include_estimates:return False
        lease=uuid.uuid4();job.lease=lease;job.lease_until=timezone.now()+timedelta(minutes=3);job.state='working';job.save()
    error='';reference=''
    try:rows,pages,total,seen=read_page(job)
    except (fb.BillingError,ValueError,TypeError,KeyError,AttributeError,ValidationError) as exc:
        error=ERROR;reference=exc.reference if isinstance(exc,SourceRecordError) else ''
    with transaction.atomic():
        lock_queue()
        current=FreshBooksMirrorJob.objects.get(pk=job.pk)
        active=selected();preferences=config(active) if active else None
        if current.lease!=lease or current.generation!=job.generation:return True
        if not(active and preferences.enabled and active.account_id==job.account_id and active.business_id==job.business_id
                and preferences.business_id==job.business_id and available(active,job.kind)
                and (job.kind!='estimates' or preferences.include_estimates)):
            current.lease=current.lease_until=None;current.save();return True
        if error:
            current.state='failed';current.error=error;current.error_reference=reference;current.next_at=timezone.now()+timedelta(minutes=15)
        else:
            for row in rows:apply_row(current,row)
            current.pages=pages;current.expected_total=total;current.seen_ids=seen
            if current.page>=pages:
                FreshBooksMirrorRecord.objects.filter(account_id=job.account_id,kind=job.kind).exclude(last_seen_generation=job.generation).update(missing=True)
                current.state='complete';current.complete_at=timezone.now();current.next_at=timezone.now()+timedelta(minutes=5)
            else:current.page+=1
        current.lease=current.lease_until=None;current.save()
    return True


def process_mirror(limit=8):
    connection=selected()
    if not connection or not config(connection).enabled:return 0
    request_sync(automatic=True);count=0;started=time.monotonic()
    for _ in range(limit):
        if time.monotonic()-started>35 or not process_step():break
        count+=1
    return count


@transaction.atomic
def link_client(*,record_id,client_id,version,actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue();connection=selected()
    if not connection:raise ValidationError('Select the FreshBooks business first.')
    record=FreshBooksMirrorRecord.objects.get(pk=record_id,account_id=connection.account_id,kind='clients')
    if record.version!=version:raise EditConflict('The source record changed. Reload before linking.')
    target=Client.objects.get(pk=client_id,merged_into__isnull=True)
    other=FreshBooksClientLink.objects.filter(account_id=record.account_id,client=target).exclude(customer_id=record.provider_id).exists()
    if other:raise ValidationError('This CRM client belongs to another FreshBooks billing identity. Keep the companies separate.')
    existing=FreshBooksClientLink.objects.filter(account_id=record.account_id,customer_id=record.provider_id).first()
    if existing and current_client(existing.client).pk!=target.pk:
        raise ValidationError('This FreshBooks client is already linked. Use the CRM merge/review tools rather than changing billing ownership here.')
    FreshBooksClientLink.objects.update_or_create(account_id=record.account_id,customer_id=record.provider_id,
        defaults={'client':target,'label':record.title[:250],'email':record.source['email'],'actor':actor})
    record.client=target;record.version+=1;sync_client(record);record.save()
    FreshBooksMirrorEvent.objects.create(account_id=record.account_id,record=record,actor=actor,action='CRM billing identity linked',details={'client':str(target.pk)})
    return record


def context_records(client=None,project=None):
    connection=selected()
    if not connection:return FreshBooksMirrorRecord.objects.none()
    rows=FreshBooksMirrorRecord.objects.filter(account_id=connection.account_id)
    if client:rows=rows.filter(client=client)
    if project:rows=rows.filter(project=project)
    return rows
