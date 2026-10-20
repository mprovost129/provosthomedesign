"""Create only approved projects. Persist write intent before every remote POST.

Uncertain writes are reconciled by a private marker, never automatically repeated.
No invoice, invitation, email, upload, payment or provider deletion endpoints exist here.
"""
import re
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from . import freshbooks as fb
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink
from .freshbooks_project_models import FreshBooksProject, FreshBooksClientProvision
from .models import AuditEvent
from .pm_services import current_client
from .services import require_queue_staff, lock_queue, EditConflict

PROJECT_SCOPES = {'user:projects:read','user:projects:write','user:clients:write'}


def norm(value):
    return re.sub(r'\W+', '', str(value or '').casefold())


def positive_id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9]\d{0,17}', str(value)):
        raise ValidationError('FreshBooks returned an invalid record reference. Review the connection.')
    return int(value)


def configuration():
    connection = FreshBooksConnection.objects.filter(pk=1,needs_reconnect=False).first()
    if not fb.configured() or not connection or not connection.encrypted_tokens or not connection.account_id:
        raise ValidationError('Connect FreshBooks and select your billing business before creating projects.')
    if not PROJECT_SCOPES.issubset(set(connection.granted_scopes)):
        raise ValidationError('Reconnect FreshBooks to approve project access and creating new billing clients.')
    return connection, positive_id(connection.business_id)


def ready():
    try:
        configuration()
        return True
    except ValidationError:
        return False


def client_snapshot(item):
    contact = item.client_contact
    if not contact or contact.archived_at:
        raise ValidationError('Connect this request to an active CRM client contact before preparing FreshBooks.')
    client = current_client(contact.client)
    if client.pk != contact.client_id:
        raise ValidationError('The CRM client was merged. Update the request contact before preparing FreshBooks.')
    # An explicitly selected billing contact takes precedence over the submitting contact.
    billing = client.contacts.filter(archived_at__isnull=True,role='billing').order_by('pk')
    if billing.count()>1:
        raise ValidationError('This client has multiple billing contacts. Select one billing contact in the CRM first.')
    chosen = billing.first() or (client.primary_contact if client.primary_contact and not client.primary_contact.archived_at else contact)
    if chosen.client_id != client.pk or not chosen.email:
        raise ValidationError('Add a billing email to this CRM client before preparing FreshBooks.')
    return {'client':str(client.pk),'version':client.version,'name':client.name,'kind':client.kind,
        'contact':str(chosen.pk),'full_name':chosen.full_name,'email':chosen.email.strip().lower(),
        'phone':chosen.phone,'street':client.billing_street,'city':client.billing_city,
        'state':client.billing_state,'zip':client.billing_zip}


def project_collision(title, remote):
    existing=str(remote.get('title') or '')
    number=title.split(' - ',1)[0]
    return norm(existing)==norm(title) or (bool(re.fullmatch(r'\d{6}',number)) and existing.startswith(number+' - '))


def all_pages(path, key, *, accounting=False, filters=None):
    """Never infer absence from an incomplete list. FreshBooks caps pages at 100."""
    items, seen, started = [], set(), time.monotonic()
    for page in range(1,201):
        if time.monotonic()-started > 40:
            raise ValidationError('The FreshBooks list took too long to check. No records were created; try again later.')
        body = fb.api('GET',path,params={**(filters or {}),'per_page':100,'page':page})
        result = body.get('response',{}).get('result',{}) if accounting else body
        batch = result.get(key)
        if not isinstance(batch,list):
            raise ValidationError('FreshBooks returned an incomplete list. No records were created.')
        for row in batch:
            identifier = positive_id(row.get('id'))
            if identifier in seen:
                raise ValidationError('FreshBooks pagination repeated a record. No records were created.')
            seen.add(identifier)
        items.extend(batch)
        meta = result if accounting else result.get('meta',{})
        pages = meta.get('pages')
        if pages is not None:
            try:
                finished = page >= int(pages)
            except (TypeError,ValueError):
                raise ValidationError('FreshBooks pagination needs review.') from None
            if finished:
                return items
        elif len(batch)<100:
            return items
    raise ValidationError('The FreshBooks list is too large to check safely. Link the billing client manually.')


def projects(business_id):
    return all_pages(f'/projects/business/{positive_id(business_id)}/projects','projects')


def remote_project(business_id, project_id):
    result = fb.api('GET',f'/projects/business/{positive_id(business_id)}/project/{positive_id(project_id)}').get('project',{})
    if positive_id(result.get('id')) != int(project_id) or result.get('active') is False:
        raise ValidationError('Choose an active FreshBooks project from this billing business.')
    return result


def identity_matches(remote, profile):
    name = remote.get('organization') if profile['kind']=='company' else ' '.join(filter(None,[remote.get('fname'),remote.get('lname')]))
    return norm(name)==norm(profile['name']) and str(remote.get('email') or '').strip().lower()==profile['email']


def resolve_client(profile, account):
    link = FreshBooksClientLink.objects.filter(client_id=profile['client'],account_id=account).first()
    if link:
        fb.remote_client(link.customer_id)
        return {'mode':'existing','id':link.customer_id,'label':link.label}
    # Check exact email and company/person name separately, including archived records.
    found = {}
    path = f'/accounting/account/{account}/users/clients'
    for state in (0,1,2):
        for filters in ({'search[email]':profile['email']},{'search[user_like]':profile['name']}):
            for row in all_pages(path,'clients',accounting=True,filters={**filters,'search[vis_state]':state}):
                found[positive_id(row.get('id'))] = row
    candidates = list(found.values())
    exact = [row for row in candidates if identity_matches(row,profile) and row.get('vis_state',0)==0]
    if len(exact)==1 and len(candidates)==1:
        customer = positive_id(exact[0]['id'])
        if FreshBooksClientLink.objects.filter(account_id=account,customer_id=customer).exclude(client_id=profile['client']).exists():
            raise ValidationError('That FreshBooks client belongs to another CRM record. Review the CRM billing connection.')
        return {'mode':'existing','id':customer,'label':exact[0].get('organization') or profile['name']}
    if candidates:
        raise ValidationError('FreshBooks has a possible client match. Use this client’s Billing connection to choose the correct company, then preview again. No duplicate client was created.')
    pending = FreshBooksClientProvision.objects.filter(account_id=account,client_id=profile['client']).first()
    if pending and pending.state not in {'queued','linked'}:
        raise ValidationError('An earlier client creation needs checking in FreshBooks. Review its existing project filing first.')
    return {'mode':'new','id':None,'label':profile['name']}


def plan(item, label, *, action='skip', pricing='fixed_price', amount=None, remote_id=None):
    if action=='skip':
        return {'action':'skip'}
    connection,business = configuration()
    profile = client_snapshot(item)
    existing = FreshBooksProject.objects.filter(project_id=item.project_id).first() if item.project_id else None
    if existing:
        if existing.client_id != item.client_contact.client_id or existing.account_id!=connection.account_id or existing.business_id!=business:
            raise ValidationError('This project already has another FreshBooks client or business. Review its existing connection.')
        return {'action':'reuse','id':str(existing.pk),'account':existing.account_id,'business':existing.business_id,
            'profile':profile,'title':existing.title,'customer':{'mode':'existing','id':existing.customer_id,'label':profile['name']},'state':existing.get_state_display()}
    customer = resolve_client(profile,connection.account_id)
    result = {'action':action,'account':connection.account_id,'business':business,'profile':profile,'customer':customer,'title':label}
    if action=='link':
        if not customer['id']:
            raise ValidationError('Connect the CRM client to an existing FreshBooks billing client before linking a project.')
        remote = remote_project(business,remote_id)
        if positive_id(remote.get('client_id'))!=customer['id']:
            raise ValidationError('That FreshBooks project belongs to a different billing client.')
        if FreshBooksProject.objects.filter(business_id=business,remote_id=remote['id']).exists():
            raise ValidationError('That FreshBooks project is already connected to another website project.')
        result.update(remote_id=positive_id(remote['id']),remote_title=str(remote['title'])[:255])
        return result
    if action!='create' or pricing not in {'fixed_price','hourly_rate'}:
        raise ValidationError('Choose Fixed price or Hourly for the FreshBooks project.')
    try:
        money = Decimal(str(amount))
        if not money.is_finite() or money<=0 or money>Decimal('999999999.99') or money.as_tuple().exponent < -2:
            raise ValueError
    except (InvalidOperation,ValueError,TypeError):
        raise ValidationError('Enter the actual fixed price or hourly rate, greater than zero with at most two decimal places.') from None
    for remote in projects(business):
        if project_collision(label,remote):
            raise ValidationError('A FreshBooks project with this name or project number already exists. Choose Connect an existing FreshBooks project and enter its project ID.')
    result['payload']={'title':label,'project_type':pricing,'fixed_price' if pricing=='fixed_price' else 'rate':format(money,'.2f')}
    if pricing=='hourly_rate':
        result['payload']['billing_method']='project_rate'
    return result


def check_plan(item, data):
    if data.get('action','skip')=='skip':
        return
    connection,business = configuration()
    if connection.account_id!=data['account'] or business!=data['business'] or client_snapshot(item)!=data['profile']:
        raise EditConflict('The FreshBooks business or client details changed. Preview filing again.')
    link = FreshBooksClientLink.objects.filter(client_id=data['profile']['client'],account_id=data['account']).first()
    if link and data['customer'].get('id')!=link.customer_id:
        raise EditConflict('The billing client connection changed. Preview filing again.')


def enqueue(item, binding, data, actor):
    if data.get('action','skip')=='skip':
        return None
    check_plan(item,data)
    existing = FreshBooksProject.objects.filter(project=item.project).first()
    if existing:
        if data['action']!='reuse' or str(existing.pk)!=data['id']:
            raise EditConflict('A FreshBooks project was already requested. Review its existing status.')
        return existing
    marker='PHD-WEB-PROJECT-'+item.project.pk.hex
    payload=dict(data.get('payload',{}))
    if payload:
        payload['description']='Project filing reference: '+marker
    job=FreshBooksProject.objects.create(project=item.project,work_item=item,client_id=data['profile']['client'],
        account_id=data['account'],business_id=data['business'],customer_id=data['customer']['id'],
        title=data.get('remote_title') or binding.intended_name,marker=marker,payload=payload,client_snapshot=data['profile'],
        remote_id=data.get('remote_id'),state='linked' if data['action']=='link' else 'queued',actor=actor)
    if job.customer_id:
        save_client_link(job,job.customer_id)
    AuditEvent.objects.create(work_item=item,actor=actor,action='freshbooks_project_approved',
        changes={'project':str(job.pk),'title':job.title,'action':data['action']})
    return job


def save_client_link(job, customer):
    with transaction.atomic():
        lock_queue()
        link,created=FreshBooksClientLink.objects.get_or_create(client=job.client,account_id=job.account_id,
            defaults={'customer_id':customer,'label':job.client_snapshot['name'],'email':job.client_snapshot['email'],'actor':job.actor})
        if link.customer_id!=customer:
            raise ValidationError('The CRM billing client changed. Review FreshBooks before continuing.')
    return link


def client_payload(profile, marker):
    names=profile['full_name'].split(' ',1)
    return {'fname':names[0],'lname':names[1] if len(names)>1 else '',
        'organization':profile['name'] if profile['kind']=='company' else '',
        'email':profile['email'],'bus_phone':profile['phone'],'p_street':profile['street'],
        'p_city':profile['city'],'p_province':profile['state'],'p_code':profile['zip'],'note':marker}


def set_state(job, state, error='', **fields):
    job.state,job.last_error=state,error
    for name,value in fields.items():
        setattr(job,name,value)
    job.save(update_fields=['state','last_error','updated_at',*fields])


def provision_client(job):
    candidate=resolve_client(job.client_snapshot,job.account_id)
    if candidate['id']:
        save_client_link(job,candidate['id'])
        return candidate['id']
    with transaction.atomic():
        lock_queue()
        intent,_=FreshBooksClientProvision.objects.get_or_create(account_id=job.account_id,client=job.client,
            defaults={'marker':'PHD-WEB-CLIENT-'+job.client.pk.hex,'payload':{}})
        intent=FreshBooksClientProvision.objects.select_for_update().get(pk=intent.pk)
        if intent.state=='linked':
            save_client_link(job,intent.remote_id)
            return intent.remote_id
        if intent.state!='queued':
            raise ValidationError('An earlier billing-client write needs reconciliation. Use Check FreshBooks; it will not be repeated.')
        intent.payload=client_payload(job.client_snapshot,intent.marker)
        intent.state,intent.attempted_at='creating',timezone.now()
        intent.save()
    # Separate committed intent: a process crash cannot erase knowledge of this POST.
    try:
        remote=fb.api('POST',f'/accounting/account/{job.account_id}/users/clients',data={'client':intent.payload}).get('response',{}).get('result',{}).get('client',{})
        customer=positive_id(remote.get('id'))
        if not identity_matches(remote,job.client_snapshot):
            raise ValidationError('FreshBooks client response did not match the approved billing details.')
    except Exception as exc:
        definite=isinstance(exc,fb.BillingError) and exc.status in {400,401,403,404,422,429}
        FreshBooksClientProvision.objects.filter(pk=intent.pk).update(state='attention' if definite else 'unknown')
        set_state(job,'attention' if definite else 'unknown','Billing-client creation was rejected. Review before retrying.' if definite else 'Billing-client creation may have succeeded. Use Check FreshBooks before taking another action.',phase='client')
        return None
    FreshBooksClientProvision.objects.filter(pk=intent.pk).update(state='linked',remote_id=customer)
    save_client_link(job,customer)
    return customer


def project_matches(job, remote, *, marker=True):
    return (positive_id(remote.get('client_id'))==job.customer_id and str(remote.get('title'))==job.title
        and (not marker or str(remote.get('description') or '').strip()=='Project filing reference: '+job.marker))


def finish(job, remote):
    identifier=positive_id(remote.get('id'))
    if not project_matches(job,remote):
        raise ValidationError('FreshBooks returned a different client, project name or integration reference. Review before linking.')
    if job.payload:
        try:
            key='fixed_price' if job.payload['project_type']=='fixed_price' else 'rate'
            if remote.get('project_type')!=job.payload['project_type'] or Decimal(str(remote.get(key)))!=Decimal(job.payload[key]):
                raise ValueError
        except (ValueError,TypeError,InvalidOperation):
            raise ValidationError('FreshBooks pricing differs from the approved project. Review it before linking.') from None
    with transaction.atomic():
        lock_queue()
        current=FreshBooksProject.objects.select_for_update().get(pk=job.pk)
        if current.remote_id and current.remote_id!=identifier:
            raise ValidationError('The FreshBooks project connection changed.')
        set_state(current,'linked',remote_id=identifier)
        AuditEvent.objects.create(work_item=job.work_item,action='freshbooks_project_connected',
            changes={'project':str(job.pk),'remote_id':identifier,'title':job.title})


def process_one():
    if not fb.configured():
        return False
    FreshBooksProject.objects.filter(state='working',attempted_at__lt=timezone.now()-timedelta(minutes=10)).update(
        state='unknown',last_error='The background step was interrupted. Use Check FreshBooks before retrying.')
    with transaction.atomic():
        # One durable working intent serializes project/client provisioning across
        # workers, including different CRM records with overlapping identities.
        connection=FreshBooksConnection.objects.select_for_update(skip_locked=True).filter(pk=1).first()
        if not connection or FreshBooksProject.objects.filter(state='working').exists():
            return False
        job=FreshBooksProject.objects.select_for_update(skip_locked=True).filter(state='queued').order_by('updated_at','pk').first()
        if not job:
            return False
        set_state(job,'working',attempted_at=timezone.now())
    try:
        connection,business=configuration()
        if job.account_id!=connection.account_id or job.business_id!=business:
            raise ValidationError('Reconnect the originally approved FreshBooks business before continuing.')
        item=job.work_item
        if item.deleted_at or item.project_id!=job.project_id or client_snapshot(item)!=job.client_snapshot:
            raise ValidationError('The request or client details changed after approval. Review the billing connection.')
        binding=job.project.filing_folder
        if not binding.folder_id:
            set_state(job,'queued','Waiting for the approved Dropbox project folder.')
            return True
        if binding.intended_name!=job.title:
            raise ValidationError('The approved Dropbox folder name changed. Review FreshBooks before continuing.')
        if not job.customer_id:
            link=FreshBooksClientLink.objects.filter(client=job.client,account_id=job.account_id).first()
            if link and not FreshBooksClientProvision.objects.filter(client=job.client,account_id=job.account_id,state='linked',remote_id=link.customer_id).exists():
                raise ValidationError('The billing-client connection changed after approval. Review the original client before continuing.')
            customer=provision_client(job)
            if customer:
                set_state(job,'queued',customer_id=customer,phase='project')
            return True
        linked=FreshBooksClientLink.objects.filter(client=job.client,account_id=job.account_id).first()
        if not linked or linked.customer_id!=job.customer_id:
            raise ValidationError('The approved billing-client connection changed. Review before continuing.')
        fb.remote_client(job.customer_id)
        existing=projects(job.business_id)
        marked=[remote for remote in existing if str(remote.get('description') or '').strip()=='Project filing reference: '+job.marker]
        if len(marked)==1:
            finish(job,marked[0])
            return True
        if marked or any(project_collision(job.title,remote) for remote in existing):
            raise ValidationError('A possible FreshBooks project already exists. Connect it after review; no duplicate was created.')
        # Commit phase before external write; a timeout/process crash cannot replay it.
        set_state(job,'working',phase='project',attempted_at=timezone.now())
        try:
            remote=fb.api('POST',f'/projects/business/{job.business_id}/project',data={'project':{**job.payload,'client_id':job.customer_id}}).get('project',{})
        except fb.BillingError as exc:
            definite=exc.status in {400,401,403,404,422,429}
            set_state(job,'attention' if definite else 'unknown','FreshBooks rejected project creation. Review before retrying.' if definite else 'Project creation may have succeeded. Use Check FreshBooks; it will not be created again automatically.')
            return True
        try:
            finish(job,remote)
        except (ValidationError,IntegrityError):
            set_state(job,'unknown','FreshBooks may have created the project, but its response could not be verified. Use Check FreshBooks.')
    except (fb.BillingError,ValidationError,IntegrityError):
        job.refresh_from_db()
        if job.state=='working':
            set_state(job,'attention','Check the original billing business, CRM client and Dropbox folder, then retry or connect an existing FreshBooks project.')
    return True


def recover(job_id, actor, *, action, remote_id=None):
    require_queue_staff(actor,'workqueue.change_workitem')
    job=FreshBooksProject.objects.get(pk=job_id)
    connection,business=configuration()
    if job.account_id!=connection.account_id or job.business_id!=business:
        raise ValidationError('Select the originally approved billing business first.')
    if job.state not in {'unknown','attention'}:
        raise ValidationError('This FreshBooks project does not need recovery. Refresh its status.')
    if action=='check':
        if not job.customer_id:
            intent=FreshBooksClientProvision.objects.filter(client=job.client,account_id=job.account_id).first()
            if not intent:
                raise ValidationError('No billing-client write was attempted. Review the CRM billing connection and retry.')
            path=f'/accounting/account/{job.account_id}/users/clients'
            rows=all_pages(path,'clients',accounting=True,filters={'search[note_like]':intent.marker})
            matches=[row for row in rows if row.get('note')==intent.marker and identity_matches(row,job.client_snapshot) and row.get('vis_state',0)==0]
            if len(matches)!=1:
                raise ValidationError('No single verified billing client was found. Check FreshBooks and connect the CRM billing client manually; no write was repeated.')
            customer=positive_id(matches[0]['id'])
            with transaction.atomic():
                lock_queue()
                save_client_link(job,customer)
                FreshBooksClientProvision.objects.filter(pk=intent.pk).update(state='linked',remote_id=customer)
                set_state(job,'queued',customer_id=customer,phase='project')
            return
        matches=[row for row in projects(business) if str(row.get('description') or '').strip()=='Project filing reference: '+job.marker]
        if len(matches)!=1:
            raise ValidationError('No single verified project was found. Review FreshBooks and connect its project ID if it exists. An uncertain create is never repeated.')
        finish(job,matches[0])
    elif action=='link':
        if not job.customer_id:
            link=FreshBooksClientLink.objects.filter(client=job.client,account_id=job.account_id).first()
            if not link:
                raise ValidationError('Connect the CRM billing client before linking the FreshBooks project.')
            job.customer_id=link.customer_id
        remote=remote_project(business,remote_id)
        if not project_matches(job,remote,marker=False):
            raise ValidationError('Choose a project with the exact approved name and billing client.')
        with transaction.atomic():
            lock_queue()
            current=FreshBooksProject.objects.select_for_update().get(pk=job.pk)
            if current.state not in {'unknown','attention'}:
                raise EditConflict('The FreshBooks project changed. Refresh before continuing.')
            set_state(current,'linked',customer_id=job.customer_id,remote_id=positive_id(remote['id']))
    elif action=='retry':
        if job.state!='attention':
            raise ValidationError('An uncertain creation cannot be retried. Check FreshBooks or connect an existing record.')
        client_snapshot(job.work_item)  # Require complete current client details.
        with transaction.atomic():
            lock_queue()
            current=FreshBooksProject.objects.select_for_update().get(pk=job.pk)
            if current.state!='attention':
                raise EditConflict('The FreshBooks project changed. Refresh before retrying.')
            # Only definitely rejected client writes are eligible; ambiguous ones stay held.
            FreshBooksClientProvision.objects.filter(client=job.client,account_id=job.account_id,state='attention').update(state='queued')
            set_state(current,'queued')
    else:
        raise ValidationError('Choose a FreshBooks recovery action.')
    AuditEvent.objects.create(work_item=job.work_item,actor=actor,action='freshbooks_project_recovery',changes={'project':str(job.pk),'action':action})
