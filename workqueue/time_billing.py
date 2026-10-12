"""Reviewed hourly invoice requests; reservations survive uncertain provider writes."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from .models import Client, WorkItem, WorkProject
from .time_models import TimeEntry
from .time_billing_models import TimeBillingBatch, TimeBillingLine, TimeBillingRate
from .freshbooks_models import FreshBooksClientLink, FreshBooksInvoice
from . import freshbooks
from .crm import require_staff
from .pm_services import current_client, event
from .services import lock_queue, EditConflict


def eligible(project=None):
    claims=TimeBillingLine.objects.filter(entry_id=OuterRef('pk'),active=True)
    entries=TimeEntry.objects.filter(deleted_at__isnull=True,billable=True,duration_seconds__gt=0)
    if project:entries=entries.filter(project=project)
    return entries.annotate(reserved=Exists(claims)).filter(reserved=False).select_related('work_item__client_contact__client','actor')


def billing_clients(project):
    ids=WorkItem.objects.filter(project=project,client_contact__isnull=False).values_list('client_contact__client_id',flat=True)
    resolved={current_client(c).pk for c in Client.objects.filter(pk__in=ids)}
    return Client.objects.filter(pk__in=resolved,merged_into__isnull=True).order_by('name','pk')


def fixed_scope(project):
    return list(project.proposals.filter(has_draft=False,current_revision__state='accepted',
        current_revision__snapshot__pricing='fixed').order_by('pk').values_list('pk','version'))


def preview(project,client,entries,rate,descriptions,notes):
    if not freshbooks.configured():raise ValidationError('FreshBooks is disabled or unconfigured. Connect it before preparing invoice drafts.')
    account=freshbooks.account_id()
    if not billing_clients(project).filter(pk=client.pk).exists():raise ValidationError('Choose a billing company connected to this project.')
    link=FreshBooksClientLink.objects.filter(client=client,account_id=account).first()
    if not link:raise ValidationError('Connect this company to its existing FreshBooks client before preparing an invoice.')
    if not 1<=len(entries)<=50:raise ValidationError('Select from 1 to 50 completed time entries.')
    rate=Decimal(rate).quantize(Decimal('.01'))
    if not rate.is_finite() or not Decimal('.01')<=rate<=Decimal('999999.99'):raise ValidationError('Enter a positive USD hourly rate up to 999,999.99.')
    rows=[]
    for entry in entries:
        if entry.project_id!=project.pk or not entry.duration_seconds or entry.deleted_at or not entry.billable or entry.running or entry.reserved:
            raise EditConflict('Selected time changed or was reserved for another invoice. Reload before preparing this draft.')
        if entry.work_item and entry.work_item.client_contact_id and current_client(entry.work_item.client_contact.client).pk!=client.pk:
            raise ValidationError('A selected job belongs to another company. Prepare separate invoices for each company.')
        description=descriptions.get(str(entry.pk),'').strip()
        if not description or len(description)>2000:raise ValidationError('Enter a client-facing description for each selected entry, up to 2,000 characters.')
        qty=(Decimal(entry.duration_seconds)/3600).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
        amount=(qty*rate).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        rows.append({'entry':str(entry.pk),'version':entry.version,'seconds':entry.duration_seconds,'date':entry.work_date.isoformat(),
            'description':description,'quantity':str(qty),'rate':str(rate),'amount':str(amount),'job':str(entry.work_item_id or '')})
    rows.sort(key=lambda r:r['entry'])
    total=sum((Decimal(r['amount']) for r in rows),Decimal(0))
    if not Decimal('.01')<=total<=Decimal('999999999999.99'):raise ValidationError('Review the selected time and rate: the invoice total must be positive and within the supported amount.')
    return {'project':str(project.pk),'project_name':project.name,'client':str(client.pk),'client_name':client.name,
        'client_version':client.version,'account':account,'customer':link.customer_id,'rate':str(rate),'rows':rows,
        'total':str(total),'currency':'USD','notes':notes.strip(),'fixed_scope':[(str(pk),v) for pk,v in fixed_scope(project)]}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@transaction.atomic
def queue_time_invoice(snapshot,batch_id,actor,remember=False,additional=False):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    existing=TimeBillingBatch.objects.filter(pk=batch_id).first()
    if existing:
        if existing.actor_id!=actor.pk or digest(existing.snapshot)!=digest(snapshot):raise ValidationError('This invoice request belongs to another preview.')
        return existing
    project=WorkProject.objects.get(pk=snapshot['project']);client=Client.objects.get(pk=snapshot['client'])
    ids=[r['entry'] for r in snapshot['rows']]
    # Queue lock serializes time edits and claims. Lock only time rows, not nullable job joins.
    list(TimeEntry.objects.select_for_update().filter(pk__in=ids))
    entries=list(eligible(project).filter(pk__in=ids))
    current=preview(project,client,entries,snapshot['rate'],{r['entry']:r['description'] for r in snapshot['rows']},snapshot['notes'])
    if digest(current)!=digest(snapshot):raise EditConflict('Time, the company, or its billing connection changed. Review a new preview before creating an invoice.')
    if current['fixed_scope'] and not additional:raise ValidationError('Confirm that these hours are additional to the accepted fixed-price scope.')
    batch=TimeBillingBatch.objects.create(id=batch_id,project=project,client=client,snapshot=current,total=current['total'],actor=actor)
    payload={'invoice':{'customerid':current['customer'],'create_date':freshbooks.timezone.localdate().isoformat(),'currency_code':'USD',
        'po_number':'PHD-TIME-'+batch.pk.hex,'notes':f"Project: {project.name}\n"+current['notes'],
        'lines':[{'name':row['description'][:100],'description':row['date']+' · '+row['description'],
                  'qty':row['quantity'],'unit_cost':{'amount':row['rate'],'code':'USD'}} for row in current['rows']]}}
    invoice=FreshBooksInvoice.objects.create(project=project,client=client,account_id=current['account'],customer_id=current['customer'],
        marker=payload['invoice']['po_number'],payload=payload,actor=actor)
    batch.invoice=invoice;batch.save(update_fields=['invoice'])
    TimeBillingLine.objects.bulk_create([TimeBillingLine(batch=batch,entry_id=row['entry'],snapshot=row) for row in current['rows']])
    if remember:TimeBillingRate.objects.update_or_create(project=project,defaults={'rate':current['rate']})
    event(project,actor,'Hourly invoice draft requested',batch=str(batch.pk),client=str(client.pk),entries=len(current['rows']),total=current['total'])
    return batch


@transaction.atomic
def release_batch(batch_id,actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    batch=TimeBillingBatch.objects.select_for_update().get(pk=batch_id)
    invoice=FreshBooksInvoice.objects.select_for_update().get(pk=batch.invoice_id)
    if batch.released_at:return batch
    if invoice.invoice_id or invoice.state not in ('queued','failed'):
        raise ValidationError('This draft may exist in FreshBooks. Check the invoice there; its time remains reserved to prevent duplicate billing.')
    invoice.state,invoice.last_error='released','Released before invoice creation.'
    invoice.save(update_fields=['state','last_error'])
    batch.released_at=freshbooks.timezone.now();batch.save(update_fields=['released_at'])
    batch.lines.update(active=False)
    event(batch.project,actor,'Hourly invoice reservation released',batch=str(batch.pk))
    return batch


def assert_time_editable(entry):
    if entry.billing_lines.filter(active=True).exists():
        raise ValidationError('This time is reserved for an invoice. Review its billing record before changing or removing it.')
