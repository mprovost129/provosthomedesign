"""Bounded, resumable GET-only FreshBooks summaries, isolated by billing account.

No invoices, payments, clients or folders are created/changed here. Only complete
validated scans replace saved totals; interrupted/failed scans preserve the last
snapshot. Provider records/notes and payment details are never persisted.
"""
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import freshbooks
from .dashboard_models import DashboardBillingSummary
from .freshbooks_models import FreshBooksConnection
from .invoice_history_sync import integer
from .invoice_overview import STATUS_LABELS
from .services import lock_queue

PAYMENTS_SCOPE = 'user:payments:read'
MAX_PAGES = 100
CHECK_ERROR = 'FreshBooks summary could not be completed. Previous checked totals are retained. Review the connection and try another check.'


def period_for(kind, today=None):
    return 'all' if kind == 'invoices' else (today or timezone.localdate()).strftime('%Y-%m')


def connection():
    if not freshbooks.configured():
        return None
    return FreshBooksConnection.objects.filter(pk=1, needs_reconnect=False).exclude(
        account_id='').exclude(encrypted_tokens='').only('account_id','business_name','granted_scopes').first()


def request_checks(actor=None, automatic=False):
    if actor:
        freshbooks.require_staff(actor, 'workqueue.change_workitem')
    now = timezone.now()
    selected = connection()
    if not selected:
        if automatic:
            return 0
        raise ValidationError('Connect FreshBooks and select your business before checking billing.')
    started = 0
    with transaction.atomic():
        lock_queue()
        current = FreshBooksConnection.objects.select_for_update().get(pk=1)
        if current.account_id != selected.account_id or current.needs_reconnect:
            return 0
        for kind in ('invoices','payments'):
            if kind == 'payments' and PAYMENTS_SCOPE not in current.granted_scopes:
                continue
            job, created = DashboardBillingSummary.objects.get_or_create(account_id=current.account_id,
                kind=kind, period=period_for(kind), defaults={'requested_at':now})
            if not created:
                if job.state in ('queued','scanning'):
                    continue
                delay = timedelta(hours=24) if automatic and job.state == 'idle' else timedelta(minutes=30 if automatic else 5)
                if job.requested_at > now-delay:
                    continue
                job.state, job.requested_at, job.finished_at = 'queued', now, None
                job.page, job.pages, job.expected_total = 1, None, None
                job.seen_ids, job.working_totals, job.working_excluded = [], {}, 0
                job.error, job.lease, job.lease_until = '', None, None
                job.save()
            started += 1
    return started


def amount(raw, currency):
    if not isinstance(raw, dict) or raw.get('code') != currency:
        raise ValueError('Currency mismatch')
    try:
        result = Decimal(str(raw['amount']))
    except (KeyError, TypeError, InvalidOperation):
        raise ValueError('Invalid amount') from None
    if not result.is_finite() or abs(result)>Decimal('999999999999.99') or result != result.quantize(Decimal('.01')):
        raise ValueError('Invalid precision')
    return result


def currency_code(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Z]{3}',value):
        raise ValueError('Invalid currency')
    return value


def add(totals, currency, key, value):
    group = totals.setdefault(currency, {})
    group[key] = str(Decimal(group.get(key,'0')) + value)


def consume_invoice(raw, job, totals):
    if raw.get('accounting_systemid') != job.account_id or raw.get('accountid') != job.account_id:
        raise ValueError('Wrong billing business')
    visibility = integer(raw.get('vis_state'))
    status = str(raw.get('v3_status') or raw.get('display_status') or '').strip().lower()
    if visibility == 1 or status in ('draft','created','voided','deleted','declined','pending'):
        return True
    if visibility not in (0,2) or status not in STATUS_LABELS:
        raise ValueError('Unrecognized invoice status')
    currency = currency_code(raw.get('currency_code'))
    outstanding = amount(raw.get('outstanding'),currency)
    if outstanding < 0:
        raise ValueError('Negative outstanding balance')
    if status in ('paid','autopaid','success') and outstanding:
        raise ValueError('Conflicting paid invoice')
    if not outstanding:
        return False
    due = date.fromisoformat(raw['due_date']) if raw.get('due_date') else None
    # Freeze the due-date boundary at the requested scan's Eastern date.
    today = timezone.localdate(job.requested_at)
    add(totals,currency,'outstanding',outstanding)
    add(totals,currency,'overdue',outstanding if status=='overdue' or due and due<today else Decimal('0'))
    return False


def consume_payment(raw, job, totals):
    if raw.get('accounting_systemid') != job.account_id:
        raise ValueError('Wrong payment business')
    visibility = integer(raw.get('vis_state'))
    if visibility == 1:
        return True
    if visibility != 0 or not isinstance(raw.get('from_credit'),bool):
        raise ValueError('Unrecognized payment')
    if raw['from_credit'] or str(raw.get('type','')).strip().lower()=='credit' or raw.get('creditid'):
        return True
    paid_date = date.fromisoformat(raw['date'])
    first = date.fromisoformat(job.period+'-01')
    cutoff = timezone.localdate(job.requested_at)
    if not first<=paid_date<=cutoff:
        raise ValueError('Payment outside requested period')
    integer(raw.get('invoiceid'),1)
    payment_amount = raw.get('amount')
    if not isinstance(payment_amount,dict):
        raise ValueError('Invalid payment amount')
    currency = currency_code(payment_amount.get('code'))
    add(totals,currency,'payments',amount(payment_amount,currency))
    return False


def read_page(job):
    endpoint = 'invoices/invoices' if job.kind=='invoices' else 'payments/payments'
    params={'page':job.page,'per_page':100}
    if job.kind=='payments':
        params.update({'search[date_min]':job.period+'-01',
            'search[date_max]':(timezone.localdate(job.requested_at)+timedelta(days=1)).isoformat(),
            'search[payment_or_credit]':'invoice'})
    body=freshbooks.api('GET',f'/accounting/account/{job.account_id}/{endpoint}',params=params)
    response=body.get('response')
    if not isinstance(response,dict):
        raise ValueError('Missing response')
    result=response.get('result')
    if not isinstance(result,dict):
        raise ValueError('Missing result')
    rows=result.get(job.kind)
    if not isinstance(rows,list):
        raise ValueError('Missing records')
    page,pages,total=(integer(result.get(k),1 if k=='page' else 0) for k in ('page','pages','total'))
    if page!=job.page or integer(result.get('per_page'),1)!=100 or pages>MAX_PAGES:
        raise ValueError('Invalid pagination')
    if pages!=max(1,(total+99)//100) and not(total==0 and pages==0):
        raise ValueError('Incomplete pagination')
    if len(rows)!=min(100,max(0,total-(page-1)*100)):
        raise ValueError('Incomplete page')
    if job.pages is not None and (job.pages!=pages or job.expected_total!=total):
        raise ValueError('Provider records changed during scan')
    seen=set(job.seen_ids)
    totals={code:dict(values) for code,values in job.working_totals.items()}
    excluded=job.working_excluded
    for raw in rows:
        if not isinstance(raw,dict):
            raise ValueError('Invalid record')
        identifier=integer(raw.get('invoiceid') if job.kind=='invoices' else raw.get('id'),1)
        if identifier in seen:
            raise ValueError('Repeated provider record')
        if job.kind=='payments' and raw.get('logid') is not None and integer(raw['logid'],1)!=identifier:
            raise ValueError('Conflicting payment identity')
        seen.add(identifier)
        excluded+=bool(consume_invoice(raw,job,totals) if job.kind=='invoices' else consume_payment(raw,job,totals))
    return pages,total,sorted(seen),totals,excluded


def process_one():
    selected=connection()
    if not selected:
        return False
    now=timezone.now()
    with transaction.atomic():
        job=DashboardBillingSummary.objects.select_for_update(skip_locked=True).filter(
            account_id=selected.account_id,state__in=['queued','scanning']).filter(
                Q(lease_until__isnull=True)|Q(lease_until__lt=now)).order_by('requested_at','kind').first()
        if not job:
            return False
        if job.kind=='payments' and PAYMENTS_SCOPE not in selected.granted_scopes:
            job.state,job.error,job.finished_at='failed','Read-only payment permission needs reconnecting.',now
            job.save(update_fields=['state','error','finished_at'])
            return True
        if job.kind=='payments' and job.period!=period_for('payments'):
            job.state,job.error,job.finished_at='failed','The month changed before this check finished. Start a new check.',now
            job.save(update_fields=['state','error','finished_at'])
            return True
        if now-job.requested_at>timedelta(hours=1):
            job.state,job.error,job.finished_at='failed','This check expired. Start a fresh check.',now
            job.save(update_fields=['state','error','finished_at'])
            return True
        job.state,job.lease,job.lease_until='scanning',uuid.uuid4(),now+timedelta(minutes=2)
        job.save(update_fields=['state','lease','lease_until'])
        lease=job.lease
    try:
        result=read_page(job)
        error=''
    except (freshbooks.BillingError,ValueError,TypeError,KeyError,InvalidOperation):
        result,error=None,CHECK_ERROR
    with transaction.atomic():
        # A different business or lost lease cannot publish into this dashboard.
        current=FreshBooksConnection.objects.select_for_update().get(pk=1)
        target=DashboardBillingSummary.objects.select_for_update().get(pk=job.pk)
        if target.lease!=lease:
            return True
        target.lease,target.lease_until=None,None
        if current.account_id!=job.account_id or current.needs_reconnect:
            error='The billing connection changed during this check. Start another check for the selected business.'
        if error:
            target.state,target.error,target.finished_at='failed',error,timezone.now()
        else:
            target.pages,target.expected_total,target.seen_ids,target.working_totals,target.working_excluded=result
            if target.page>=target.pages:
                target.totals=target.working_totals
                target.record_count,target.excluded_count=len(target.seen_ids),target.working_excluded
                target.checked_at,target.finished_at=timezone.now(),timezone.now()
                target.state,target.error='idle',''
                target.seen_ids,target.working_totals=[],{}
            else:
                target.page+=1
        target.save()
    return True


def sync_summaries():
    request_checks(automatic=True)
    return int(process_one())


def summary_context():
    now=timezone.now()
    selected=connection()
    rows=[]
    for kind in ('invoices','payments'):
        row=DashboardBillingSummary.objects.filter(account_id=selected.account_id if selected else '',
            kind=kind,period=period_for(kind)).defer('seen_ids','working_totals').first()
        rows.append({'kind':kind,'snapshot':row,
            'totals':[{'currency':code,**values} for code,values in sorted(row.totals.items())] if row and row.checked_at else [],
            'stale':bool(row and row.checked_at and (row.checked_at<now-timedelta(hours=24) or row.error)),
            'pending':bool(row and row.state in ('queued','scanning'))})
    return {'connected':bool(selected),'business_name':selected.business_name if selected else '',
        'payment_access':bool(selected and PAYMENTS_SCOPE in selected.granted_scopes),
        'invoice':rows[0],'payment':rows[1]}
