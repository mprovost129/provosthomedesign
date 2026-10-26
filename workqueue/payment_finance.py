"""Read-only Stripe cost reconciliation. Never writes payments, refunds or invoice balances."""
import hashlib,json
from datetime import datetime,timedelta,time,timezone as utc
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .billing_mail import require_owner
from .payment_finance_models import NativeFinanceSettings,NativePaymentFinance,NativeBalanceEntry,NativeFinanceEvent
from .test_payment_models import NativePaymentAttempt
from .test_payment_gateway import TestStripeGateway,GatewayUnavailable,configuration
from .test_refunds import parsed_adjustments,provider_id
from .services import lock_queue,EditConflict

EASTERN=ZoneInfo('America/New_York')
MESSAGES={
 'awaiting_fees':'Stripe has not supplied the complete fee transactions yet. Fees and net are unknown.',
 'awaiting_accounting':'Payment or adjustment accounting must finish before fees can reconcile.',
 'provider_unavailable':'Stripe fee history could not be read. Check the connection and Balance read permission.',
 'history_mismatch':'The provider history did not match this invoice. Previously recorded figures were preserved.',
 'stale_read':'Accounting changed during the check. Refresh again to reconcile the latest history.',
 'changed_transaction':'A previously recorded Stripe transaction changed or disappeared. Review the provider record.',
}
class FinanceIssue(Exception):
    def __init__(self,code):self.code=code;super().__init__(MESSAGES[code])

def settings_record():
    return NativeFinanceSettings.objects.filter(pk=1).first() or NativeFinanceSettings(pk=1)

@transaction.atomic
def save_settings(*,actor,version,enabled):
    require_owner(actor);lock_queue();row=settings_record()
    if row.version!=version:raise EditConflict('Fee settings changed. Reload before saving.')
    if type(enabled) is not bool:raise ValidationError('Choose whether to enable fee reconciliation.')
    row.enabled=enabled;row.version+=1;row.changed_at=timezone.now();row.changed_by=actor;row.full_clean();row.save()
    NativeFinanceEvent.objects.create(actor=actor,action='Fee reconciliation enabled' if enabled else 'Fee reconciliation paused',details={'version':row.version,'test_only':True})
    return row

def fingerprint(attempt):
    # Include immutable client-principal entries plus adjustment/request states. Reading Stripe alone does not invalidate a verified snapshot.
    data={'invoice_version':attempt.invoice.version,'state':attempt.state,'issue':attempt.issue_code,
      'entries':[(e.pk,e.kind,e.amount,e.adjustment_id,e.reverses_id) for e in sorted(attempt.invoice.entries.all(),key=lambda e:str(e.pk))],
      'adjustments':[(e.provider_id,e.kind,e.amount,e.status,e.applied_amount) for e in sorted(attempt.adjustments.all(),key=lambda e:str(e.pk))],
      'refunds':[(e.pk,e.state,e.provider_id,e.amount) for e in sorted(attempt.refund_requests.all(),key=lambda e:str(e.pk))]}
    return hashlib.sha256(json.dumps(data,default=str,sort_keys=True).encode()).hexdigest()

def current(row):
    return row.state=='verified' and row.fingerprint==fingerprint(row.attempt)

@transaction.atomic
def begin(attempt_id):
    lock_queue();attempt=NativePaymentAttempt.objects.select_related('invoice','ledger_entry').get(pk=attempt_id)
    row,_=NativePaymentFinance.objects.get_or_create(attempt=attempt)
    row.generation+=1;row.state='pending';row.issue='';row.checked_at=timezone.now();row.save()
    return attempt,row.generation,fingerprint(attempt),attempt.adjustment_generation

def cents(value):return Decimal(value)/Decimal(100)

def identifier(value,prefix=None):
    return provider_id(value.get('id') if isinstance(value,dict) else value,prefix)

def timestamp(value):
    if type(value) is not int or not 0<value<4102444800:raise FinanceIssue('history_mismatch')
    return datetime.fromtimestamp(value,utc.utc)

def read_entries(attempt,gateway):
    if not attempt.ledger_entry_id or attempt.issue_code or attempt.state!='paid':raise FinanceIssue('awaiting_accounting')
    if gateway.account_id()!=attempt.account_id:raise FinanceIssue('history_mismatch')
    session=gateway.retrieve(attempt.session_id)
    intent=session.get('payment_intent') or {};charge=intent.get('latest_charge') or {}
    refunds,disputes=gateway.adjustments(attempt.payment_intent_id,charge.get('id'),disputes=True)
    parsed=parsed_adjustments(attempt,session,refunds,disputes)
    if (session.get('payment_status')!='paid' or session.get('status')!='complete' or intent.get('status')!='succeeded'
       or intent.get('livemode') is not False or intent.get('amount_received')!=int(attempt.amount*100)
       or intent.get('currency')!='usd' or intent.get('metadata',{}).get('phd_test_attempt')!=str(attempt.pk)
       or intent.get('metadata',{}).get('purpose')!='phd_native_invoice_sandbox'
       or charge.get('paid') is not True or charge.get('status')!='succeeded'):
        raise FinanceIssue('history_mismatch')
    if (attempt.ledger_entry.kind!='payment' or attempt.ledger_entry.amount!=attempt.amount or attempt.ledger_entry.invoice_id!=attempt.invoice_id
       or attempt.ledger_entry.reference!=attempt.payment_intent_id or attempt.invoice.entries.filter(reverses_id=attempt.ledger_entry_id).exists()):
        raise FinanceIssue('history_mismatch')
    known={a.provider_id:a for a in attempt.adjustments.all()}
    if set(known)!={obj['id'] for obj in parsed}:raise FinanceIssue('awaiting_accounting')
    for obj in parsed:
        local=known[obj['id']]
        if local.status!=obj['status'] or local.applied_amount!=obj['target']:raise FinanceIssue('awaiting_accounting')
    if attempt.refund_requests.filter(state__in=['creating','pending','requires_action','review']).exists():raise FinanceIssue('awaiting_accounting')
    refs=[]
    def add(ref,source,kind,expected=None):
        if not ref:raise FinanceIssue('awaiting_fees')
        refs.append((identifier(ref,'txn'),source,kind,expected))
    add(charge.get('balance_transaction'),charge['id'],'payment',int(attempt.amount*100))
    for obj in refunds:
        if obj['status'] in {'pending','requires_action'}:raise FinanceIssue('awaiting_accounting')
        ref=obj.get('balance_transaction');failure=obj.get('failure_balance_transaction')
        if obj['status']=='succeeded':
            if failure:raise FinanceIssue('history_mismatch')
            add(ref,obj['id'],'refund',-obj['amount'])
        elif ref or failure:
            add(ref,obj['id'],'refund',-obj['amount']);add(failure,obj['id'],'refund_failure',obj['amount'])
    for obj in disputes:
        for ref in obj['balance_transactions']:add(ref,obj['id'],'dispute')
    rows=[];seen=set()
    for tid,source,kind,expected in refs:
        if tid in seen:raise FinanceIssue('history_mismatch')
        seen.add(tid);obj=gateway.balance_transaction(tid)
        amount,fee,net=(obj.get(k) for k in ('amount','fee','net'))
        if (obj.get('object')!='balance_transaction' or obj.get('id')!=tid or obj.get('currency')!='usd'
           or obj.get('exchange_rate') is not None or identifier(obj.get('source'))!=source
           or any(type(n) is not int or abs(n)>10**14 for n in (amount,fee,net)) or amount-fee!=net
           or (expected is not None and amount!=expected) or obj.get('status') not in {'pending','available'}
           or obj.get('type') not in {'charge','payment','refund','payment_refund','refund_failure','payment_failure_refund','adjustment'}):
            raise FinanceIssue('history_mismatch')
        details=obj.get('fee_details')
        if not isinstance(details,list) or len(details)>100:raise FinanceIssue('history_mismatch')
        detail_rows=[]
        for detail in details:
            if (not isinstance(detail,dict) or type(detail.get('amount')) is not int or detail.get('currency')!='usd'
               or not isinstance(detail.get('type'),str)):raise FinanceIssue('history_mismatch')
            detail_rows.append({'amount':detail['amount'],'currency':'usd','type':detail['type'][:80]})
        if sum(d['amount'] for d in detail_rows)!=fee:raise FinanceIssue('history_mismatch')
        rows.append(dict(provider_id=tid,account_id=attempt.account_id,source_id=source,kind=kind,provider_type=obj['type'],
            currency='usd',amount=amount,fee=fee,net=net,fee_details=detail_rows,
            occurred_at=timestamp(obj.get('created')),available_at=timestamp(obj.get('available_on')),availability=obj['status']))
    expected=int(attempt.amount*100)-sum(int(obj['target']*100) for obj in parsed)
    actual=attempt.amount
    for entry in attempt.invoice.entries.filter(adjustment__attempt=attempt,kind__in=['refund','return','recovery']):
        actual+=entry.amount if entry.kind=='recovery' else -entry.amount
    if sum(row['amount'] for row in rows)!=expected or int(actual*100)!=expected:raise FinanceIssue('history_mismatch')
    return rows

@transaction.atomic
def finish(attempt_id,generation,stamp,adjustment_generation,rows=None,issue=''):
    lock_queue();row=NativePaymentFinance.objects.select_related('attempt__invoice').get(pk=attempt_id)
    if row.generation!=generation:return row
    if row.attempt.adjustment_generation!=adjustment_generation or fingerprint(row.attempt)!=stamp:
        issue='stale_read';rows=None
    if rows is not None:
        previous={e.provider_id:e for e in row.entries.all()}
        if set(previous)-{r['provider_id'] for r in rows}:issue='changed_transaction'
        for data in rows:
            old=previous.get(data['provider_id'])
            if old and any(getattr(old,k)!=v for k,v in data.items() if k!='availability'):issue='changed_transaction'
            if NativeBalanceEntry.objects.exclude(finance=row).filter(provider_id=data['provider_id']).exists():issue='history_mismatch'
        if not issue:
            for data in rows:
                old=previous.get(data['provider_id'])
                if old:
                    if old.availability!=data['availability']:
                        old.availability=data['availability'];old.save(update_fields=['availability'])
                else:NativeBalanceEntry.objects.create(finance=row,**data)
            row.state='verified';row.fingerprint=stamp;row.verified_at=timezone.now()
    if issue:row.state='pending' if issue in {'awaiting_fees','awaiting_accounting','provider_unavailable','stale_read'} else 'review'
    changed=(row.issue!=issue or row.state=='verified' and any(r['provider_id'] not in previous for r in rows or []))
    row.issue=issue;row.checked_at=timezone.now();row.save()
    if changed:NativeFinanceEvent.objects.create(finance=row,action='Fee history reconciled' if not issue else 'Fee check needs attention',details={'issue':issue,'transactions':[r['provider_id'] for r in rows or []]})
    return row

def reconcile(attempt_id,*,gateway=None):
    if not settings_record().enabled:raise ValidationError('Read-only sandbox fee reconciliation is paused.')
    gateway=gateway or TestStripeGateway()
    attempt,generation,stamp,adjustment_generation=begin(attempt_id)
    try:rows=read_entries(attempt,gateway)
    except FinanceIssue as exc:return finish(attempt_id,generation,stamp,adjustment_generation,issue=exc.code)
    except GatewayUnavailable:return finish(attempt_id,generation,stamp,adjustment_generation,issue='provider_unavailable')
    except (ValidationError,ValueError,TypeError,KeyError,AttributeError):
        return finish(attempt_id,generation,stamp,adjustment_generation,issue='history_mismatch')
    return finish(attempt_id,generation,stamp,adjustment_generation,rows=rows)

def process_pending(limit=2):
    if not settings_record().enabled or not configuration()['ready']:return {'checked':0,'attention':0}
    gateway=TestStripeGateway();cutoff=timezone.now()-timedelta(minutes=15)
    ids=list(NativePaymentAttempt.objects.filter(ledger_entry__isnull=False).filter(
        Q(finance__isnull=True)|Q(finance__checked_at__lt=cutoff)|Q(finance__checked_at__isnull=True))
        .order_by('finance__checked_at','created_at').values_list('pk',flat=True)[:limit])
    attention=0
    for pk in ids:
        row=reconcile(pk,gateway=gateway);attention+=row.state!='verified'
    return {'checked':len(ids),'attention':attention}

def report(start,end,query=''):
    if start>end or (end-start).days>366:raise ValidationError('Choose a date range of up to one year.')
    first=datetime.combine(start,time.min,EASTERN);last=datetime.combine(end+timedelta(days=1),time.min,EASTERN)
    attempts=NativePaymentAttempt.objects.filter(ledger_entry__isnull=False).select_related('invoice','finance').prefetch_related('invoice__entries','adjustments','refund_requests').order_by('-created_at')
    if query:attempts=attempts.filter(Q(invoice__number__icontains=query)|Q(invoice__snapshot__client_name__icontains=query)|Q(invoice__snapshot__project_name__icontains=query))
    verified=[];unresolved=[];history=[];checked=[]
    for attempt in attempts:
        row=getattr(attempt,'finance',None)
        ok=bool(row and current(row));history.append({'attempt':attempt,'finance':row,'current':ok})
        if not ok:unresolved.append(attempt)
        else:verified.append(attempt.pk);checked.append(row.checked_at)
    entries=list(NativeBalanceEntry.objects.filter(finance_id__in=verified,occurred_at__gte=first,occurred_at__lt=last).select_related('finance__attempt__invoice'))
    gross=sum(e.amount for e in entries if e.kind=='payment')
    changes=sum(e.amount for e in entries if e.kind!='payment')
    fees=sum(e.fee for e in entries);net=sum(e.net for e in entries)
    assert gross+changes-fees==net
    return {'gross':cents(gross),'adjustments':cents(changes),'fees':None if unresolved else cents(fees),
      'net':None if unresolved else cents(net),'verified_fees':cents(fees),'verified_net':cents(net),
      'oldest_check':min(checked) if checked else None,'newest_check':max(checked) if checked else None,
      'unresolved':len(unresolved),'history':history,'entries':entries,'count':len(entries),'start':start,'end':end,
      'pending_available':cents(sum(e.net for e in entries if e.availability=='pending'))}
