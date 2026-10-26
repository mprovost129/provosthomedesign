"""Read-only provider settlement checks; bank confirmation never posts invoice revenue."""
import hashlib,json,re
from datetime import timedelta
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .billing_mail import require_owner
from .payment_finance import cents,timestamp,identifier,current as finance_current,EASTERN
from .payment_finance_models import NativeBalanceEntry
from .payout_models import NativePayoutSettings,NativePayout,NativePayoutTransaction,NativeBankConfirmation,NativePayoutEvent
from .test_payment_models import NativePaymentAttempt
from .test_payment_gateway import configuration,TestStripeGateway,GatewayUnavailable
from .services import lock_queue,EditConflict

MESSAGES={
 'provider_unavailable':'The complete Stripe payout history could not be read. Previously verified evidence was preserved.',
 'history_mismatch':'The payout or transaction details do not safely match. Review Stripe before confirming a deposit.',
 'amount_mismatch':'The included transaction net does not equal the payout plus its payout fee. No balanced total was accepted.',
 'not_paid':'Stripe has not confirmed this payout as paid. A bank deposit must not be assumed.',
 'payout_failed':'Stripe reports this payout as failed or canceled. Any earlier bank confirmation needs review.',
 'not_ready':'Stripe is still assigning transactions to this automatic payout.',
 'unsupported':'Manual, instant, reversed and non-USD payouts require a separate reconciliation method.',
 'changed_history':'Previously verified payout economics changed or a transaction disappeared. Review is required.',
 'stale_read':'The payout changed during this check. Refresh again.',
 'duplicate_membership':'A transaction is already assigned to another balanced, paid payout. Review both payouts.',
}
class PayoutIssue(Exception):
    def __init__(self,code):self.code=code;super().__init__(MESSAGES[code])

def settings_record():return NativePayoutSettings.objects.filter(pk=1).first() or NativePayoutSettings(pk=1)

@transaction.atomic
def save_settings(*,actor,version,enabled):
    require_owner(actor);lock_queue();row=settings_record()
    if version!=row.version:raise EditConflict('Payout settings changed. Reload before saving.')
    if type(enabled) is not bool:raise ValidationError('Choose whether to enable payout checks.')
    row.enabled=enabled;row.version+=1;row.changed_at=timezone.now();row.changed_by=actor;row.full_clean();row.save()
    NativePayoutEvent.objects.create(actor=actor,action='Read-only payout checks enabled' if enabled else 'Read-only payout checks paused',details={'version':row.version,'test_only':True})
    return row

def require_enabled():
    if not settings_record().enabled:raise ValidationError('Read-only sandbox payout checks are paused.')

def account(gateway):
    actual=gateway.account_id()
    known=set(NativePaymentAttempt.objects.filter(ledger_entry__isnull=False).values_list('account_id',flat=True))
    if known!={actual}:raise ValidationError('Use the same Stripe sandbox account as the confirmed invoice payments.')
    return actual

def integer(value):
    if type(value) is not int or abs(value)>10**14:raise PayoutIssue('history_mismatch')
    return value

def metadata(obj):
    if not isinstance(obj,dict) or obj.get('object')!='payout' or obj.get('livemode') is not False:raise PayoutIssue('history_mismatch')
    pid=identifier(obj.get('id'),'po');amount=integer(obj.get('amount'));currency=obj.get('currency')
    if (not isinstance(currency,str) or not re.fullmatch('[a-z]{3}',currency) or type(obj.get('automatic')) is not bool
      or obj.get('status') not in {'pending','in_transit','paid','failed','canceled'} or obj.get('method') not in {'standard','instant'}
      or obj.get('reconciliation_status') not in {'completed','in_progress','not_applicable'}):raise PayoutIssue('history_mismatch')
    # Persist no destination account, bank name, account digits, description or raw metadata.
    return {'provider_id':pid,'amount':amount,'currency':currency,'automatic':obj['automatic'],'status':obj['status'],
      'method':obj['method'],'reconciliation_status':obj['reconciliation_status'],
      'provider_created':timestamp(obj.get('created')).isoformat(),
      'arrival_date':timestamp(obj.get('arrival_date')).date().isoformat(),
      'balance_transaction':identifier(obj['balance_transaction'],'txn') if obj.get('balance_transaction') else '',
      'failure_balance_transaction':identifier(obj['failure_balance_transaction'],'txn') if obj.get('failure_balance_transaction') else '',
      'reversed':bool(obj.get('reversed_by') or obj.get('original_payout'))}

def save_metadata(data,account_id):
    # Internal: caller holds QueueState. Financial identity never changes silently.
    row=NativePayout.objects.filter(provider_id=data['provider_id']).first()
    if row and (row.account_id!=account_id or row.amount!=data['amount'] or row.currency!=data['currency']):raise PayoutIssue('changed_history')
    if not row:row=NativePayout(provider_id=data['provider_id'],account_id=account_id,amount=data['amount'],currency=data['currency'])
    changed=row.provider_snapshot!=data
    for key in ['status','automatic','method','reconciliation_status']:setattr(row,key,data[key])
    from datetime import datetime,date
    row.provider_created=datetime.fromisoformat(data['provider_created']);row.arrival_date=date.fromisoformat(data['arrival_date'])
    row.provider_snapshot=data
    if changed:
        row.version+=1
        if row.status!='paid':row.state='pending';row.issue='payout_failed' if row.status in {'failed','canceled'} else 'not_paid'
        elif row.economics_hash:row.state='pending';row.issue='stale_read'
    row.save();return row

@transaction.atomic
def apply_discovery(data,account_id,since):
    lock_queue()
    for item in data:
        existing=NativePayout.objects.filter(provider_id=item['provider_id']).first()
        if existing and (existing.account_id!=account_id or existing.amount!=item['amount'] or existing.currency!=item['currency']):raise PayoutIssue('changed_history')
    rows=[save_metadata(item,account_id) for item in data]
    config=settings_record();config.discovery_at=timezone.now();config.discovery_since=since;config.discovery_issue='';config.save()
    NativePayoutEvent.objects.create(action='Recent sandbox payouts discovered',details={'count':len(rows),'since':since.isoformat(),'account':account_id})
    return rows

def discover(*,gateway=None):
    require_enabled();gateway=gateway or TestStripeGateway();since=timezone.now()-timedelta(days=90)
    try:
        aid=account(gateway);raw=gateway.recent_payouts(since)
        if not isinstance(raw,list) or len(raw)>250:raise PayoutIssue('history_mismatch')
        data=[metadata(obj) for obj in raw]
        if len({obj['provider_id'] for obj in data})!=len(data):raise PayoutIssue('history_mismatch')
        return apply_discovery(data,aid,since)
    except (GatewayUnavailable,PayoutIssue,ValidationError) as exc:
        NativePayoutSettings.objects.filter(pk=1).update(discovery_issue='provider_unavailable')
        raise ValidationError('Recent payout discovery is incomplete. No partial list was accepted; check Stripe access and try again.') from None

@transaction.atomic
def begin(payout_id):
    lock_queue();row=NativePayout.objects.get(pk=payout_id)
    row.generation+=1;row.save(update_fields=['generation'])
    return row,row.generation,row.version

def parse_transaction(obj):
    if not isinstance(obj,dict) or obj.get('object')!='balance_transaction' or obj.get('currency')!='usd' or obj.get('exchange_rate') is not None:raise PayoutIssue('history_mismatch')
    amount,fee,net=[integer(obj.get(k)) for k in ['amount','fee','net']]
    if amount-fee!=net or obj.get('status') not in {'available','pending'}:raise PayoutIssue('history_mismatch')
    details=obj.get('fee_details')
    if not isinstance(details,list) or len(details)>100:raise PayoutIssue('history_mismatch')
    for part in details:
        if not isinstance(part,dict) or part.get('currency')!='usd':raise PayoutIssue('history_mismatch')
        integer(part.get('amount'))
    if sum(part['amount'] for part in details)!=fee:raise PayoutIssue('history_mismatch')
    kind=obj.get('type')
    if not isinstance(kind,str) or not re.fullmatch('[a-z_]{1,60}',kind):raise PayoutIssue('history_mismatch')
    return {'provider_id':identifier(obj.get('id'),'txn'),'source_id':identifier(obj['source']) if obj.get('source') else '',
      'kind':kind,'amount':amount,'fee':fee,'net':net,'currency':'usd','occurred_at':timestamp(obj.get('created'))}

def read_payout(row,gateway):
    if account(gateway)!=row.account_id:raise PayoutIssue('history_mismatch')
    data=metadata(gateway.payout(row.provider_id))
    if data['provider_id']!=row.provider_id or data['amount']!=row.amount or data['currency']!=row.currency:raise PayoutIssue('changed_history')
    if data['status'] in {'failed','canceled'}:return data,[],None,'payout_failed'
    if (not data['automatic'] or data['method']!='standard' or data['currency']!='usd' or data['amount']<=0 or data['reversed']):return data,[],None,'unsupported'
    if data['status']!='paid':return data,[],None,'not_paid'
    if data['reconciliation_status']!='completed':return data,[],None,'not_ready'
    if not data['balance_transaction'] or data['failure_balance_transaction']:raise PayoutIssue('history_mismatch')
    debit=parse_transaction(gateway.balance_transaction(data['balance_transaction']))
    if debit['provider_id']!=data['balance_transaction'] or debit['kind']!='payout' or debit['source_id']!=row.provider_id or debit['amount']!=-row.amount:raise PayoutIssue('history_mismatch')
    raw=gateway.payout_transactions(row.provider_id)
    if not isinstance(raw,list) or len(raw)>1000:raise PayoutIssue('history_mismatch')
    rows=[];seen=set()
    for obj in raw:
        item=parse_transaction(obj)
        if item['provider_id'] in seen:raise PayoutIssue('history_mismatch')
        seen.add(item['provider_id'])
        if item['provider_id']==debit['provider_id']:
            if item!=debit:raise PayoutIssue('history_mismatch')
            continue  # Some provider views include the payout's own debit; never subtract it twice.
        if item['kind']=='payout':raise PayoutIssue('history_mismatch')
        rows.append(item)
    if not rows or sum(item['net'] for item in rows)+debit['net']!=0:raise PayoutIssue('amount_mismatch')
    return data,rows,debit,''

def economics(data,rows,debit):
    normalize=lambda item:{k:(value.isoformat() if hasattr(value,'isoformat') else value) for k,value in item.items()}
    raw={'account_currency':data['currency'],'amount':data['amount'],'debit':normalize(debit),'rows':[normalize(item) for item in sorted(rows,key=lambda row:row['provider_id'])]}
    return hashlib.sha256(json.dumps(raw,sort_keys=True,default=str).encode()).hexdigest()

@transaction.atomic
def finish(payout_id,generation,version,*,data=None,rows=None,debit=None,issue=''):
    lock_queue();row=NativePayout.objects.get(pk=payout_id)
    if row.generation!=generation:return row
    if row.version!=version:issue='stale_read';rows=None;data=None
    before=(row.state,row.issue,row.economics_hash,row.provider_snapshot)
    if data:
        try:row=save_metadata(data,row.account_id)
        except PayoutIssue:issue='changed_history';rows=None
    if not issue and rows is not None and debit:
        signature=economics(data,rows,debit)
        if row.economics_hash and row.economics_hash!=signature:issue='changed_history'
        ids=[item['provider_id'] for item in rows]
        if NativePayoutTransaction.objects.exclude(payout=row).filter(provider_id__in=ids,payout__state='verified',payout__status='paid').exists():issue='duplicate_membership'
        if not issue:
            for item in rows:NativePayoutTransaction.objects.get_or_create(payout=row,provider_id=item['provider_id'],defaults={k:v for k,v in item.items() if k!='provider_id'})
            row.economics_hash=signature;row.debit_snapshot={k:(value.isoformat() if hasattr(value,'isoformat') else value) for k,value in debit.items()};row.payout_transaction_id=debit['provider_id'];row.debit_amount=debit['amount'];row.debit_fee=debit['fee'];row.debit_net=debit['net']
            row.state='verified';row.verified_at=timezone.now()
    if issue:row.state='unsupported' if issue=='unsupported' else 'pending' if issue in {'provider_unavailable','not_paid','not_ready','stale_read'} else 'review'
    row.issue=issue;row.checked_at=timezone.now()
    if before!=(row.state,row.issue,row.economics_hash,row.provider_snapshot):row.version+=1
    row.save()
    if before!=(row.state,row.issue,row.economics_hash,row.provider_snapshot):
        NativePayoutEvent.objects.create(payout=row,action='Stripe payout balanced' if not issue else 'Payout check needs attention',details={'issue':issue,'transaction_count':len(rows or []),'test_only':True})
    return row

def reconcile(payout_id,*,gateway=None):
    require_enabled();gateway=gateway or TestStripeGateway();row,generation,version=begin(payout_id)
    try:data,rows,debit,issue=read_payout(row,gateway)
    except PayoutIssue as exc:return finish(row.pk,generation,version,issue=exc.code)
    except GatewayUnavailable:return finish(row.pk,generation,version,issue='provider_unavailable')
    except (ValidationError,ValueError,TypeError,KeyError,AttributeError):return finish(row.pk,generation,version,issue='history_mismatch')
    return finish(row.pk,generation,version,data=data,rows=rows,debit=debit,issue=issue)

def lookup(provider_id,*,gateway=None):
    require_enabled();identifier(provider_id,'po');gateway=gateway or TestStripeGateway();aid=account(gateway)
    try:data=metadata(gateway.payout(provider_id))
    except PayoutIssue:raise ValidationError('This is not a supported sandbox payout record.') from None
    if data['provider_id']!=provider_id:raise ValidationError('Stripe returned a different payout reference.')
    with transaction.atomic():lock_queue();row=save_metadata(data,aid)
    return reconcile(row.pk,gateway=gateway)

def local_links(row):
    entries=list(row.transactions.all());ids=[item.provider_id for item in entries]
    links={entry.provider_id:entry for entry in NativeBalanceEntry.objects.filter(provider_id__in=ids).select_related('finance__attempt__invoice').prefetch_related('finance__attempt__invoice__entries','finance__attempt__adjustments','finance__attempt__refund_requests')}
    local=0;unlinked=0;issues=0
    for item in entries:
        link=links.get(item.provider_id);item.local_entry=link;item.amount_display=cents(item.amount);item.fee_display=cents(item.fee);item.net_display=cents(item.net)
        if link:
            item.local_current=(link.account_id==row.account_id and link.currency=='usd' and link.source_id==item.source_id and link.provider_type==item.kind and (link.amount,link.fee,link.net)==(item.amount,item.fee,item.net) and finance_current(link.finance))
            issues+=not item.local_current;local+=item.net
        else:unlinked+=item.net;item.local_current=False
    return {'entries':entries,'local_net':cents(local),'unlinked_net':cents(unlinked),'local_issues':issues}

def stripe_balanced(row):
    entries=list(row.transactions.all())
    if not (row.state=='verified' and row.status=='paid' and row.currency=='usd' and row.economics_hash and row.debit_net is not None
      and sum(item.net for item in entries)+row.debit_net==0):return False
    data=[{k:getattr(item,k) for k in ['provider_id','source_id','kind','amount','fee','net','currency','occurred_at']} for item in entries]
    return economics(row.provider_snapshot,data,row.debit_snapshot)==row.economics_hash

def active_confirmation(row):return next((item for item in row.bank_confirmations.all() if item.revoked_at is None),None)

def bank_state(row):
    record=active_confirmation(row)
    if not record:return 'unconfirmed'
    return 'confirmed' if stripe_balanced(row) and record.economics_hash==row.economics_hash and record.amount==row.amount else 'review'

@transaction.atomic
def confirm_bank(*,payout_id,actor,version,amount,deposit_date,reference,token,confirm):
    require_owner(actor);lock_queue();row=NativePayout.objects.get(pk=payout_id)
    try:
        value=Decimal(str(amount));minor=value*100
        if not value.is_finite() or minor!=minor.to_integral_value() or not 0<minor<=10**14:raise ValueError
        minor=int(minor)
    except (InvalidOperation,ValueError,TypeError):raise ValidationError('Enter the exact deposit amount with at most two decimal places.') from None
    reference=str(reference or '').strip()
    if not reference or len(reference)>100 or any(ord(c)<32 for c in reference):raise ValidationError('Enter a short bank statement reference.')
    if confirm is not True:raise ValidationError('Confirm that you checked the matching deposit. Sandbox confirmations are fictional.')
    existing=NativeBankConfirmation.objects.filter(pk=token).first()
    if existing:
        if (existing.payout_id,existing.actor_id,existing.amount,existing.date,existing.reference)!=(row.pk,actor.pk,minor,deposit_date,reference):raise ValidationError('This confirmation reference belongs to different details.')
        return existing
    if row.version!=version:raise EditConflict('This payout changed. Reload and review the bank confirmation again.')
    if not stripe_balanced(row) or local_links(row)['local_issues']:raise ValidationError('Resolve Stripe and linked invoice reconciliation before confirming a bank deposit.')
    if not row.checked_at or timezone.now()-row.checked_at>timedelta(hours=1):raise ValidationError('Refresh Stripe before confirming this deposit; the last check is over an hour old.')
    if minor!=row.amount:raise ValidationError(f'The deposit differs from this payout by ${cents(minor-row.amount):.2f}. Review your bank statement before confirming.')
    today=timezone.now().astimezone(EASTERN).date()
    if not hasattr(deposit_date,'year') or deposit_date>today or deposit_date<row.provider_created.astimezone(EASTERN).date()-timedelta(days=1):raise ValidationError('Use the actual statement date, from the payout date through today.')
    if active_confirmation(row):raise ValidationError('This payout already has an active bank confirmation. Undo it before recording a correction.')
    record=NativeBankConfirmation.objects.create(id=token,payout=row,amount=minor,date=deposit_date,reference=reference,economics_hash=row.economics_hash,actor=actor)
    row.version+=1;row.save(update_fields=['version'])
    NativePayoutEvent.objects.create(payout=row,actor=actor,action='Fictional sandbox bank receipt confirmed',details={'confirmation':str(record.pk),'amount':minor,'date':deposit_date.isoformat(),'test_only':True})
    return record

@transaction.atomic
def undo_bank(*,payout_id,actor,version,reason):
    require_owner(actor);lock_queue();row=NativePayout.objects.get(pk=payout_id)
    if row.version!=version:raise EditConflict('This payout changed. Reload before correcting the confirmation.')
    reason=str(reason or '').strip()
    if not reason or len(reason)>500:raise ValidationError('Explain the correction in up to 500 characters.')
    record=active_confirmation(row)
    if not record:raise ValidationError('There is no active bank confirmation to undo.')
    record.revoked_at=timezone.now();record.revoked_by=actor;record.revoke_reason=reason;record.save(update_fields=['revoked_at','revoked_by','revoke_reason'])
    row.version+=1;row.save(update_fields=['version'])
    NativePayoutEvent.objects.create(payout=row,actor=actor,action='Bank confirmation corrected; history retained',details={'confirmation':str(record.pk),'reason':reason})
    return record

def process_pending(limit=2):
    if not settings_record().enabled or not configuration()['ready']:return {'checked':0,'attention':0,'discovered':0}
    gateway=TestStripeGateway();config=settings_record();discovered=0;attention=0
    if not config.discovery_at or timezone.now()-config.discovery_at>timedelta(hours=1):
        try:discovered=len(discover(gateway=gateway))
        except ValidationError:attention+=1
    cutoff=timezone.now()-timedelta(minutes=15)
    ids=list(NativePayout.objects.filter(Q(checked_at__isnull=True)|Q(checked_at__lt=cutoff)).order_by('checked_at','provider_created').values_list('pk',flat=True)[:limit])
    for pk in ids:
        row=reconcile(pk,gateway=gateway);attention+=row.state not in {'verified','unsupported'}
    return {'checked':len(ids),'attention':attention,'discovered':discovered}
