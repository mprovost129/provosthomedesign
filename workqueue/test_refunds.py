"""Provider-verified sandbox refunds, returns and recoveries; no live payments."""
import re
from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .crm import require_staff
from .native_invoice_models import NativeInvoiceEntry, NativeInvoiceEvent
from .native_invoices import money, text, next_number, balance
from .services import lock_queue, EditConflict
from .test_payment_models import NativePaymentAttempt, NativeStripeEvent, RESERVED_STATES
from .test_refund_models import NativeRefundRequest, NativePaymentAdjustment, REFUND_HOLDS
from .test_payment_gateway import TestStripeGateway, GatewayUnavailable, configuration

ZERO=Decimal('0.00')
DISPUTE_OPEN={'needs_response','under_review','warning_needs_response','warning_under_review'}
REFUND_STATES={'pending','requires_action','succeeded','failed','canceled'}
DISPUTE_STATES=DISPUTE_OPEN|{'lost','won','warning_closed','prevented'}


def refund_owner(actor):
    require_staff(actor,'workqueue.change_workitem')
    if not actor.is_superuser:raise PermissionDenied
    if not configuration()['refunds_enabled']:
        raise ValidationError('Issuing sandbox refunds is not enabled. You can still review adjustment history.')


def refund_hold(invoice):
    return NativeRefundRequest.objects.filter(attempt__invoice=invoice,state__in=REFUND_HOLDS).exists()


@transaction.atomic
def begin_read(attempt_id):
    lock_queue()
    attempt=NativePaymentAttempt.objects.get(pk=attempt_id)
    attempt.adjustment_generation+=1
    attempt.save(update_fields=['adjustment_generation'])
    return attempt.adjustment_generation


def provider_id(value,prefix):
    if not isinstance(value,str) or not re.fullmatch(prefix+r'_[A-Za-z0-9]{1,220}',value):
        raise ValidationError('The provider adjustment reference could not be verified.')
    return value


def parsed_adjustments(attempt, session, refunds, disputes):
    from .test_payments import valid_session
    intent=session.get('payment_intent') if isinstance(session,dict) else None
    charge=intent.get('latest_charge') if isinstance(intent,dict) else None
    if not valid_session(attempt,session) or not isinstance(charge,dict):
        raise ValidationError('The adjusted payment could not be verified.')
    charge_id=provider_id(charge.get('id'),'ch')
    intent_id=provider_id(intent.get('id'),'pi')
    if attempt.payment_intent_id and intent_id!=attempt.payment_intent_id:
        raise ValidationError('The adjustment belongs to a different payment.')
    if charge.get('livemode') is not False or charge.get('currency')!='usd' or charge.get('amount')!=int(attempt.amount*100):
        raise ValidationError('The adjusted charge does not match the test payment.')
    if not isinstance(refunds,list) or not isinstance(disputes,list):raise ValidationError('Incomplete provider history.')
    result=[];seen=set()
    for kind,objects in [('refund',refunds),('dispute',disputes)]:
        for obj in objects:
            if not isinstance(obj,dict):raise ValidationError('Invalid provider history.')
            pid=provider_id(obj.get('id'),'re' if kind=='refund' else 'du')
            cents=obj.get('amount');status=obj.get('status')
            if (pid in seen or obj.get('object')!=kind or obj.get('currency')!='usd' or obj.get('charge')!=charge_id
                or (obj.get('payment_intent') is not None and obj.get('payment_intent')!=intent_id)
                or type(cents) is not int or not 0<cents<=int(attempt.amount*100)
                or status not in (REFUND_STATES if kind=='refund' else DISPUTE_STATES)
                or obj.get('livemode',False) is not False):
                raise ValidationError('A refund or dispute does not match this sandbox payment.')
            seen.add(pid);amount=Decimal(cents)/100
            target=amount if kind=='refund' and status=='succeeded' else ZERO
            if kind=='dispute':
                transactions=obj.get('balance_transactions')
                if not isinstance(transactions,list) or len(transactions)>2:raise ValidationError('Dispute funds are not confirmed.')
                ids=set();net=0
                for row in transactions:
                    tid=provider_id(row.get('id') if isinstance(row,dict) else None,'txn')
                    if tid in ids or row.get('object')!='balance_transaction' or row.get('currency')!='usd' or type(row.get('amount')) is not int:
                        raise ValidationError('Dispute funds do not match the invoice currency.')
                    ids.add(tid);net+=row['amount']  # Principal only. Fees are not client debt.
                target=Decimal(-net)/100
                if not ZERO<=target<=amount:raise ValidationError('Dispute principal requires manual review.')
                if status in {'won','warning_closed','prevented'} and target:
                    raise ValidationError('The closed dispute still has unconfirmed funds.')
                if status=='lost' and not target:raise ValidationError('The lost dispute withdrawal is not confirmed yet.')
            metadata=obj.get('metadata') or {}
            request_token=metadata.get('phd_test_refund') if isinstance(metadata,dict) and metadata.get('purpose')=='phd_native_invoice_sandbox' else None
            result.append({'id':pid,'kind':kind,'amount':amount,'status':status,'target':target,'request_token':request_token})
    if sum((row['target'] for row in result),ZERO)>attempt.amount:
        raise ValidationError('Refunds and dispute withdrawals overlap. Review the provider record before changing this balance.')
    if charge.get('disputed') and not disputes:raise ValidationError('The dispute history has not arrived yet.')
    recorded_refunds=sum((row['amount'] for row in result if row['kind']=='refund'),ZERO)
    if charge.get('amount_refunded',0)>int(recorded_refunds*100):raise ValidationError('The refund history has not arrived yet.')
    return result


def ledger(invoice, adjustment, kind, amount, *, note, actor=None, reverses=None):
    entry=NativeInvoiceEntry(invoice=invoice,adjustment=adjustment,kind=kind,amount=amount,
        date=timezone.localdate(),method=adjustment.attempt.method if kind in {'refund','return','recovery'} else '',
        reference=adjustment.provider_id,note=note,actor=actor,reverses=reverses,
        credit_number=next_number('last_credit','CR') if kind=='credit' else None)
    entry.full_clean(exclude=['actor'] if actor is None else None);entry.save()
    return entry


@transaction.atomic
def reconcile(attempt_id,session,refunds,disputes,*,account_id,generation,event_id='',event_kind=''):
    from .test_payments import apply_session
    lock_queue()
    attempt=NativePaymentAttempt.objects.select_related('invoice').get(pk=attempt_id)
    if generation!=attempt.adjustment_generation:return attempt
    if event_id and NativeStripeEvent.objects.filter(event_id=event_id).exists():return attempt
    if account_id!=attempt.account_id:raise ValidationError('Restore this payment\'s matching Stripe sandbox account.')
    rows=parsed_adjustments(attempt,session,refunds,disputes)
    known={a.provider_id:a for a in attempt.adjustments.all()}
    if set(known)-{row['id'] for row in rows}:raise ValidationError('Provider adjustment history is incomplete. Existing accounting was preserved.')
    # Validate request bindings before any ledger change. Dashboard refunds have no request.
    for row in rows:
        request=NativeRefundRequest.objects.filter(provider_id=row['id']).first()
        if not request and row['request_token']:
            try:token=UUID(row['request_token'])
            except (ValueError,TypeError,AttributeError):raise ValidationError('Invalid refund request reference.')
            request=NativeRefundRequest.objects.filter(pk=token).first()
        if request and (request.attempt_id!=attempt.pk or request.amount!=row['amount'] or row['kind']!='refund'
                        or (request.provider_id and request.provider_id!=row['id'])):
            raise ValidationError('This refund belongs to different reviewed details.')
        row['request']=request
        if row['id'] in known and (known[row['id']].attempt_id!=attempt.pk or known[row['id']].amount!=row['amount'] or known[row['id']].kind!=row['kind']):
            raise ValidationError('Provider adjustment details changed unexpectedly.')
        if NativePaymentAdjustment.objects.exclude(attempt=attempt).filter(provider_id=row['id']).exists():
            raise ValidationError('This adjustment is already connected to another payment.')
    attempt=apply_session(attempt_id,session,account_id=account_id,adjustments_verified=True)
    if not attempt.ledger_entry_id or attempt.issue_code:
        raise ValidationError('The original payment must be safely recorded before its adjustments.')
    invoice=attempt.invoice;changed=False
    for row in rows:
        request=row['request']
        adj=known.get(row['id']) or NativePaymentAdjustment(attempt=attempt,provider_id=row['id'],kind=row['kind'],amount=row['amount'])
        previous=adj.status;old_amount=adj.applied_amount
        if adj.request_id and (not request or request.pk!=adj.request_id):raise ValidationError('The refund request association changed.')
        adj.status=row['status'];adj.request=request;adj.checked_at=timezone.now();adj.save()
        delta=row['target']-old_amount
        if delta:
            ledger(invoice,adj,'refund' if adj.kind=='refund' and delta>0 else 'return' if delta>0 else 'recovery',abs(delta),
                note='Stripe sandbox verified this adjustment; no real money moved.')
            adj.applied_amount=row['target'];changed=True
            if request and request.credit_work:
                if delta>0:
                    if adj.credit_entry_id:raise ValidationError('Review the previous refund credit before proceeding.')
                    adj.credit_entry=ledger(invoice,adj,'credit',delta,note='TEST credit for work canceled with the confirmed sandbox refund.',actor=request.actor)
                elif adj.credit_entry_id:
                    ledger(invoice,adj,'reversal',adj.credit_entry.amount,note='TEST refund failed or was reversed; its linked credit was reversed.',reverses=adj.credit_entry)
                    adj.credit_entry=None
            adj.save(update_fields=['applied_amount','credit_entry'])
        if request:
            request.provider_id=adj.provider_id;request.state=adj.status;request.issue_code='';request.save()
        if delta or previous!=adj.status:
            NativeInvoiceEvent.objects.create(invoice=invoice,action='Test adjustment reconciled',details={
                'reference':adj.provider_id,'kind':adj.kind,'status':adj.status,'change':str(delta),'credit_work':bool(request and request.credit_work)})
    if changed:invoice.version+=1;invoice.save(update_fields=['version'])
    held=any(row['kind']=='dispute' and row['status'] in DISPUTE_OPEN for row in rows)
    held=held or any(row['kind']=='refund' and row['status'] in {'pending','requires_action'} for row in rows)
    held=held or refund_hold(invoice)
    attempt.state='review' if held else 'paid';attempt.issue_code='adjustment_pending' if held else ''
    attempt.checkout_url='';attempt.last_checked_at=timezone.now();attempt.save()
    if event_id:NativeStripeEvent.objects.create(event_id=event_id,attempt=attempt,kind=event_kind)
    return attempt


@transaction.atomic
def reserve_refund(*,attempt_id,version,amount,credit_work,note,token,actor):
    refund_owner(actor);lock_queue();amount=money(amount);note=text(note,2000,'the refund reason')
    if type(credit_work) is not bool:raise ValidationError('Choose how to handle the canceled work.')
    existing=NativeRefundRequest.objects.filter(pk=token).first()
    if existing:
        if (existing.attempt_id,existing.invoice_version,existing.amount,existing.credit_work,existing.note,existing.actor_id)!=(attempt_id,version,amount,credit_work,note,actor.pk):
            raise ValidationError('This refund reference belongs to different reviewed details.')
        return existing
    attempt=NativePaymentAttempt.objects.select_related('invoice').get(pk=attempt_id)
    invoice=attempt.invoice
    if invoice.version!=version:raise EditConflict('This test invoice changed. Reload and review the refund again.')
    if not attempt.ledger_entry_id or attempt.state!='paid' or attempt.issue_code or invoice.voided_at:
        raise ValidationError('Refresh and resolve the test payment before refunding it.')
    if refund_hold(invoice) or NativePaymentAttempt.objects.filter(invoice=invoice,state__in=RESERVED_STATES).exists():
        raise ValidationError('Another checkout, refund or dispute is pending for this invoice.')
    returned=attempt.adjustments.aggregate(value=Sum('applied_amount'))['value'] or ZERO
    if amount>attempt.amount-returned:raise ValidationError('This exceeds the remaining refundable test payment.')
    if credit_work and amount>invoice.amount-balance(invoice)['credited']:
        raise ValidationError('This exceeds the remaining invoice amount that can be credited.')
    request=NativeRefundRequest(id=token,attempt=attempt,invoice_version=version,amount=amount,credit_work=credit_work,note=note,actor=actor)
    request.full_clean();request.save()
    invoice.version+=1;invoice.save(update_fields=['version'])
    NativeInvoiceEvent.objects.create(invoice=invoice,actor=actor,action='Test refund requested',details={
        'request':str(request.pk),'amount':str(amount),'credit_work':credit_work,'note':note})
    return request


def submit_refund(request,*,actor,gateway=None):
    refund_owner(actor)
    if request.actor_id!=actor.pk:raise PermissionDenied
    gateway=gateway or TestStripeGateway()
    from .test_payments import account_matches, refresh
    account_matches(gateway,request.attempt)
    request.refresh_from_db()
    if request.provider_id or request.state!='creating':
        refresh(request.attempt,gateway=gateway);request.refresh_from_db();return request
    if timezone.now()-request.created_at>timedelta(hours=23):
        raise ValidationError('Do not retry an old uncertain refund. Refresh its history to recover the exact provider reference first.')
    try:
        obj=gateway.create_refund(request)
        # Never settle from the create response. Retrieve complete current history.
        provider_id(obj.get('id'),'re')
        refresh(request.attempt,gateway=gateway)
    except (GatewayUnavailable,ValidationError):
        NativeRefundRequest.objects.filter(pk=request.pk,state='creating').update(issue_code='confirmation_uncertain')
        raise
    request.refresh_from_db()
    if not request.provider_id:
        raise GatewayUnavailable('The test refund is not confirmed in Stripe history yet. Refresh this same request; do not create another.')
    return request
