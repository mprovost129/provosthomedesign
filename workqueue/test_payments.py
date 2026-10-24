"""Reserved, idempotent sandbox settlement. Never changes real receivables."""
import re
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .crm import require_staff
from .native_invoice_models import NativeInvoice, NativeInvoiceEntry, NativeInvoiceEvent
from .native_invoices import balance
from .services import lock_queue, EditConflict
from .test_payment_models import NativePaymentAttempt, NativeStripeEvent, RESERVED_STATES
from .test_payment_gateway import TestStripeGateway, GatewayUnavailable, safe_checkout_url

PURPOSE = 'phd_native_invoice_sandbox'


def assert_not_reserved(invoice):
    if NativePaymentAttempt.objects.filter(invoice=invoice, state__in=RESERVED_STATES).exists():
        raise ValidationError('A Stripe test checkout is open, pending or needs review. Open its payment status before editing this invoice.')


@transaction.atomic
def reserve(*, invoice_id, version, method, token, actor, account_id):
    require_staff(actor, 'workqueue.change_workitem');lock_queue()
    if method not in {'card', 'bank'}:
        raise ValidationError('Choose a card or ACH test payment.')
    existing = NativePaymentAttempt.objects.filter(pk=token).first()
    if existing:
        if (existing.invoice_id != invoice_id or existing.actor_id != actor.pk or existing.method != method
                or existing.invoice_version != version or existing.account_id != account_id):
            raise ValidationError('This checkout reference belongs to a different request. Reload the invoice.')
        return existing
    invoice = NativeInvoice.objects.get(pk=invoice_id)
    if invoice.version != version:
        raise EditConflict('The test invoice changed. Reload before opening checkout.')
    if invoice.voided_at:
        raise ValidationError('A voided invoice cannot receive a test payment.')
    assert_not_reserved(invoice)
    amount = balance(invoice)['due']
    if amount < Decimal('.50') or amount > Decimal('999999.99'):
        raise ValidationError('Test the full remaining invoice balance between $0.50 and $999,999.99. Use installment invoices for deposits.')
    attempt = NativePaymentAttempt(id=token, invoice=invoice, invoice_version=version, amount=amount,
        method=method, account_id=account_id, expires_at=timezone.now()+timedelta(hours=1), actor=actor)
    attempt.full_clean();attempt.save()
    invoice.version += 1;invoice.save(update_fields=['version'])
    NativeInvoiceEvent.objects.create(invoice=invoice, actor=actor, action='Test checkout reserved',
        details={'attempt':str(attempt.pk), 'amount':str(amount), 'method':method})
    return attempt


def account_matches(gateway, attempt):
    if gateway.account_id() != attempt.account_id:
        raise ValidationError('This checkout belongs to a different Stripe sandbox account. Restore the matching connection before continuing.')


@transaction.atomic
def mark_issue(attempt_id, code):
    lock_queue()
    attempt = NativePaymentAttempt.objects.get(pk=attempt_id)
    if attempt.issue_code != 'provider_adjustment':attempt.issue_code = code
    attempt.last_checked_at = timezone.now()
    attempt.save(update_fields=['issue_code','last_checked_at','updated_at'])


def create_or_resume(attempt, *, gateway=None):
    gateway = gateway or TestStripeGateway()
    account_matches(gateway, attempt)
    if attempt.session_id:
        return refresh(attempt, gateway=gateway)
    if timezone.now()-attempt.created_at > timedelta(hours=23):
        mark_issue(attempt.pk, 'creation_needs_recovery')
        raise ValidationError('Do not retry an old uncertain checkout. Recover its Stripe session reference from the sandbox dashboard first.')
    try:
        session = gateway.create(attempt)
    except GatewayUnavailable:
        mark_issue(attempt.pk, 'creation_uncertain')
        raise
    apply_session(attempt.pk, session, account_id=gateway.account_id())
    attempt.refresh_from_db()
    return attempt


def valid_session(attempt, session):
    if not isinstance(session,dict) or not isinstance(session.get('metadata'),dict):return False
    expected = {'phd_test_attempt':str(attempt.pk), 'purpose':PURPOSE}
    session_id = session.get('id', '')
    return (session.get('object') == 'checkout.session' and session.get('livemode') is False
        and isinstance(session_id,str) and bool(re.fullmatch(r'cs_test_[A-Za-z0-9]+', session_id))
        and (not attempt.session_id or attempt.session_id == session_id)
        and session.get('mode') == 'payment' and session.get('currency') == 'usd'
        and type(session.get('amount_total')) is int and session['amount_total'] == int(attempt.amount*100)
        and session.get('client_reference_id') == str(attempt.pk)
        and session.get('metadata', {}).get('phd_test_attempt') == expected['phd_test_attempt']
        and session.get('metadata', {}).get('purpose') == PURPOSE
        and session.get('payment_method_types') == ['card' if attempt.method=='card' else 'us_bank_account'])


@transaction.atomic
def apply_session(attempt_id, session, *, account_id, event_id='', event_kind=''):
    lock_queue()
    attempt = NativePaymentAttempt.objects.select_related('invoice').get(pk=attempt_id)
    if event_id and NativeStripeEvent.objects.filter(event_id=event_id).exists():
        return attempt
    previous = attempt.state
    invoice = attempt.invoice
    code = ''
    if account_id != attempt.account_id or not valid_session(attempt, session):
        code = 'session_mismatch'
    else:
        session_id = session['id']
        if NativePaymentAttempt.objects.exclude(pk=attempt.pk).filter(session_id=session_id).exists():
            code = 'session_mismatch'
        else:
            attempt.session_id = session_id
            intent = session.get('payment_intent')
            intent_id = intent.get('id') if isinstance(intent,dict) else intent
            if intent_id:
                if not isinstance(intent_id,str) or not re.fullmatch(r'pi_[A-Za-z0-9]+',intent_id) or (attempt.payment_intent_id and attempt.payment_intent_id != intent_id):
                    code = 'intent_mismatch'
                elif NativePaymentAttempt.objects.exclude(pk=attempt.pk).filter(payment_intent_id=intent_id).exists():
                    code = 'intent_mismatch'
                else:
                    attempt.payment_intent_id = intent_id
            charge = intent.get('latest_charge') if isinstance(intent,dict) else None
            if isinstance(charge,dict) and (charge.get('amount_refunded',0) or charge.get('disputed')):
                code = 'provider_adjustment'
            elif attempt.issue_code == 'provider_adjustment':
                # Refund/dispute accounting is intentionally not silently cleared.
                code = 'provider_adjustment'
            elif not code:
                if session.get('payment_status') == 'paid':
                    valid_intent = (session.get('status') == 'complete' and isinstance(intent,dict) and intent.get('livemode') is False
                        and intent.get('currency') == 'usd' and intent.get('status') == 'succeeded'
                        and type(intent.get('amount_received')) is int and intent['amount_received'] == int(attempt.amount*100)
                        and isinstance(charge,dict) and charge.get('livemode') is False and charge.get('paid') is True
                        and charge.get('currency') == 'usd' and charge.get('status') == 'succeeded'
                        and type(charge.get('amount')) is int and charge['amount'] == int(attempt.amount*100)
                        and intent.get('metadata',{}).get('phd_test_attempt') == str(attempt.pk)
                        and intent.get('metadata',{}).get('purpose') == PURPOSE)
                    if not valid_intent:
                        code = 'settlement_unconfirmed'
                    elif not attempt.ledger_entry_id:
                        competing = NativePaymentAttempt.objects.filter(invoice=invoice,state__in=RESERVED_STATES).exclude(pk=attempt.pk).exists()
                        if competing or invoice.voided_at or balance(invoice)['due'] < attempt.amount:
                            code = 'balance_changed'
                        else:
                            entry = NativeInvoiceEntry(invoice=invoice, kind='payment', amount=attempt.amount,
                                method=attempt.method, reference=attempt.payment_intent_id,
                                note='Stripe sandbox confirmed this TEST payment; no real money received.', date=timezone.localdate(), actor=attempt.actor)
                            entry.full_clean(exclude=['actor'] if attempt.actor_id is None else None);entry.save()
                            attempt.ledger_entry = entry
                            invoice.version += 1;invoice.save(update_fields=['version'])
                    if not code:attempt.state = 'paid'
                elif attempt.ledger_entry_id:
                    # A delayed old event must never undo a confirmed payment.
                    attempt.state = 'paid'
                elif session.get('status') == 'expired':
                    attempt.state = 'expired'
                elif session.get('status') == 'complete' and isinstance(intent,dict) and intent.get('status') == 'canceled':
                    attempt.state = 'failed'
                elif session.get('status') == 'complete' and isinstance(intent,dict) and intent.get('status') == 'requires_payment_method':
                    attempt.state = 'failed'
                elif attempt.state in {'failed','expired'}:
                    pass  # A stale open/pending read cannot revive a closed attempt.
                elif session.get('status') == 'complete' and session.get('payment_status') == 'unpaid':
                    attempt.state = 'processing'
                elif session.get('status') == 'open' and session.get('payment_status') == 'unpaid':
                    url = session.get('url')
                    if not safe_checkout_url(url):code = 'checkout_address_invalid'
                    else:
                        attempt.state = 'open';attempt.checkout_url = url
                else:
                    code = 'settlement_unconfirmed'
    if code:attempt.state = 'review'
    if attempt.state != 'open':attempt.checkout_url = ''
    attempt.issue_code = code
    attempt.last_checked_at = timezone.now()
    attempt.save()
    if previous != attempt.state:
        NativeInvoiceEvent.objects.create(invoice=invoice, action='Test payment status changed',
            details={'attempt':str(attempt.pk),'status':attempt.state,'issue':code})
    if event_id:
        NativeStripeEvent.objects.create(event_id=event_id, attempt=attempt, kind=event_kind)
    return attempt


def refresh(attempt, *, gateway=None, expire=False, recovery_session=''):
    gateway = gateway or TestStripeGateway()
    account_matches(gateway, attempt)
    session_id = recovery_session or attempt.session_id
    if not session_id or not re.fullmatch(r'cs_test_[A-Za-z0-9]+', session_id):
        raise ValidationError('Retry the same checkout, or recover its exact cs_test reference from Stripe. Do not start a second attempt.')
    try:
        session = gateway.expire(session_id) if expire else gateway.retrieve(session_id)
    except GatewayUnavailable:
        mark_issue(attempt.pk, 'confirmation_unavailable')
        raise
    return apply_session(attempt.pk, session, account_id=gateway.account_id())


def start(*, invoice_id, version, method, token, actor):
    require_staff(actor, 'workqueue.change_workitem')
    gateway = TestStripeGateway()
    attempt = reserve(invoice_id=invoice_id, version=version, method=method, token=token, actor=actor, account_id=gateway.account_id())
    return create_or_resume(attempt, gateway=gateway)


def process_pending(*, limit=10):
    gateway = TestStripeGateway()
    cutoff = timezone.now()-timedelta(minutes=5)
    attempts = NativePaymentAttempt.objects.filter(session_id__isnull=False).filter(
        Q(last_checked_at__isnull=True)|Q(last_checked_at__lt=cutoff)).exclude(state__in=['expired','failed']).order_by('last_checked_at','created_at')[:limit]
    checked = failed = 0
    for attempt in attempts:
        try:
            refresh(attempt, gateway=gateway);checked += 1
        except (GatewayUnavailable,ValidationError):
            mark_issue(attempt.pk, 'confirmation_unavailable');failed += 1
    return checked, failed


def handle_event(event):
    """Caller verifies the raw signature. Never settle from the event body alone."""
    if not isinstance(event,dict) or event.get('livemode') is not False or event.get('account'):
        raise ValidationError('Only this direct Stripe sandbox is supported.')
    kind = event.get('type','')
    event_id = event.get('id','')
    if not isinstance(event_id,str) or not re.fullmatch(r'evt_[A-Za-z0-9]{1,200}', event_id):
        raise ValidationError('Invalid event reference.')
    if NativeStripeEvent.objects.filter(event_id=event_id).exists():return
    data = event.get('data')
    obj = data.get('object') if isinstance(data,dict) else None
    if not isinstance(obj,dict):return
    if kind in {'checkout.session.completed','checkout.session.async_payment_succeeded','checkout.session.async_payment_failed','checkout.session.expired'}:
        session_id = obj.get('id','')
        if not isinstance(session_id,str) or not session_id.startswith('cs_test_'):return
        attempt = NativePaymentAttempt.objects.filter(session_id=session_id).first()
        if attempt is None:
            from uuid import UUID
            metadata = obj.get('metadata')
            if not isinstance(metadata,dict):return
            try:token=UUID(metadata.get('phd_test_attempt',''))
            except (ValueError,TypeError,AttributeError):return
            attempt = NativePaymentAttempt.objects.filter(pk=token,session_id__isnull=True).first()
        if attempt is None:return
        gateway = TestStripeGateway();account_matches(gateway,attempt)
        session = gateway.retrieve(session_id)
        apply_session(attempt.pk,session,account_id=gateway.account_id(),event_id=event_id,event_kind=kind)
    elif kind in {'charge.refunded','charge.dispute.created','charge.dispute.closed'}:
        intent_id = obj.get('payment_intent')
        if not isinstance(intent_id,str):return
        with transaction.atomic():
            lock_queue()
            if NativeStripeEvent.objects.filter(event_id=event_id).exists():return
            attempt = NativePaymentAttempt.objects.filter(payment_intent_id=intent_id).first()
            if attempt is None:return
            attempt.state='review';attempt.issue_code='provider_adjustment';attempt.checkout_url=''
            attempt.save(update_fields=['state','issue_code','checkout_url','updated_at'])
            NativeStripeEvent.objects.create(event_id=event_id,attempt=attempt,kind=kind)
            NativeInvoiceEvent.objects.create(invoice=attempt.invoice,action='Test payment needs review',
                details={'attempt':str(attempt.pk),'reason':'Provider refund or dispute; ledger is unchanged.'})
