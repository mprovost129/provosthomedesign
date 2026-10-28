"""Exact-cent, read-only monthly review. Never issues billing or moves funds."""
import calendar
import copy
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from .billing_mail import require_owner
from .crm import require_staff
from .services import EditConflict, lock_queue
from .monthly_finance_models import NativeMonthState, NativeMonthSnapshot, NativeFinancialReview
from .native_invoice_models import NativeInvoice, NativeInvoiceEntry
from .payment_finance_models import NativeBalanceEntry
from .payment_finance import EASTERN, current as finance_current, settings_record
from .payout_reconciliation import account, parse_transaction, PayoutIssue
from .test_payment_models import NativePaymentAttempt
from .test_payment_gateway import TestStripeGateway, GatewayUnavailable

PAYMENTS = {'charge', 'payment'}
REFUNDS = {'refund', 'payment_refund', 'refund_failure', 'payment_failure_refund', 'payment_reversal'}
COSTS = {'stripe_fee', 'stripe_fx_fee', 'tax_fee'}
TRANSFERS = {'payout', 'payout_failure', 'payout_cancel'}
KNOWN = PAYMENTS | REFUNDS | COSTS | TRANSFERS
LIMIT = 2000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def month_dates(value):
    if isinstance(value, date):
        value = value.strftime('%Y-%m')
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValidationError('Choose a valid month.')
    try:
        start = date.fromisoformat(value + '-01')
    except ValueError:
        raise ValidationError('Choose a valid month.') from None
    today = timezone.now().astimezone(EASTERN).date()
    if start.year < 2000 or start > today.replace(day=1):
        raise ValidationError('Choose a month from 2000 through the current month.')
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def bounds(start, end):
    return datetime.combine(start, time.min, EASTERN), datetime.combine(end + timedelta(days=1), time.min, EASTERN)


def minor(value):
    value = Decimal(value) * 100
    if not value.is_finite() or value != value.to_integral_value():
        raise ValidationError('A saved money amount is not in exact cents. Review the source record.')
    return int(value)


def checked_snapshot(snapshot):
    if snapshot.checksum != digest(snapshot.rows):
        raise ValidationError('Saved Stripe activity failed its integrity check. Refresh the month before using it.')
    return snapshot.rows


def refresh_month(*, month, token, actor, gateway=None):
    require_staff(actor, 'workqueue.change_workitem')
    start, end = month_dates(month)
    if not settings_record().enabled:
        raise ValidationError('Enable the existing sandbox fee connection before reading monthly activity.')
    with transaction.atomic():
        lock_queue()
        existing = NativeMonthSnapshot.objects.filter(pk=token).first()
        if existing:
            if existing.actor_id != actor.pk or existing.month_id != start:
                raise ValidationError('This refresh reference belongs to another request.')
            return existing
        state, _ = NativeMonthState.objects.get_or_create(month=start)
        state.generation += 1
        state.save(update_fields=['generation'])
        generation = state.generation
    try:
        gateway = gateway or TestStripeGateway()
        aid = account(gateway)
        if state.account_id and state.account_id != aid:
            raise ValidationError('The monthly Stripe account changed.')
        first, last = bounds(start, end)
        raw = gateway.month_activity(first, last)
        if not isinstance(raw, list) or len(raw) > LIMIT:
            raise ValidationError('Monthly activity is incomplete; no partial totals were accepted.')
        rows = []
        seen = set()
        for obj in raw:
            item = parse_transaction(obj)
            if (item['provider_id'] in seen or not first <= item['occurred_at'] < last
                    or item['occurred_at'] > timezone.now() + timedelta(seconds=5)):
                raise ValidationError('The monthly transaction history did not match the requested period.')
            seen.add(item['provider_id'])
            item['occurred_at'] = item['occurred_at'].isoformat()
            item['availability'] = obj['status']
            rows.append(item)
        rows.sort(key=lambda row: (row['occurred_at'], row['provider_id']))
    except (GatewayUnavailable, ValidationError, PayoutIssue, ValueError, TypeError, KeyError, AttributeError):
        fail_month(start, generation)
        raise ValidationError('The complete monthly Stripe activity could not be verified. Previous evidence was retained; check the sandbox connection and try again.') from None
    except Exception:
        fail_month(start, generation)
        raise
    with transaction.atomic():
        lock_queue()
        state = NativeMonthState.objects.get(pk=start)
        if state.generation != generation:
            raise EditConflict('A newer refresh started. Reload to use its result.')
        if state.account_id and state.account_id != aid:
            raise ValidationError('The Stripe account changed. Prior evidence was retained for review.')
        old = state.snapshots.first()
        prior = {r['provider_id']: {k: v for k, v in r.items() if k != 'availability'} for r in checked_snapshot(old)} if old else {}
        incoming = {r['provider_id']: {k: v for k, v in r.items() if k != 'availability'} for r in rows}
        changed = any(key not in incoming or incoming[key] != value for key, value in prior.items())
        snapshot = NativeMonthSnapshot.objects.create(id=token, month=state, account_id=aid, rows=rows,
            checksum=digest(rows), history_changed=changed or bool(old and old.history_changed), actor=actor)
        state.account_id = aid; state.issue = ''; state.checked_at = timezone.now(); state.version += 1; state.save()
        return snapshot


@transaction.atomic
def fail_month(start, generation):
    lock_queue()
    NativeMonthState.objects.filter(pk=start, generation=generation).update(issue='refresh_failed', checked_at=timezone.now())


def report(month):
    # A single database snapshot prevents a concurrent payment from being half included.
    outer = not connection.in_atomic_block
    with transaction.atomic():
        if outer and connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return build_report(month)


def build_report(month):
    start, end = month_dates(month)
    cutoff = min(end, timezone.now().astimezone(EASTERN).date())
    first, last = bounds(start, end)
    state = NativeMonthState.objects.filter(pk=start).first()
    source = state.snapshots.first() if state else None
    rows = copy.deepcopy(checked_snapshot(source)) if source else []
    issues = []
    if not source: issues.append('Read the complete Stripe activity for this month first.')
    if state and state.issue: issues.append('The latest Stripe refresh failed or its account changed; saved figures are historical evidence.')
    if source and source.history_changed: issues.append('Previously saved Stripe transactions changed or disappeared. Review the provider history.')
    totals = {name: 0 for name in ['stripe_payments','stripe_refunds','standalone_costs','stripe_fees','stripe_costs',
        'other_movements','payout_movements','net_before_payouts','stripe_balance_movement','pending_net',
        'offline_payments','offline_corrections','credits','credit_corrections','issued','outstanding','credit_balances','overdue']}
    for row in rows:
        if any(type(row.get(k)) is not int for k in ['amount','fee','net']) or row['amount'] - row['fee'] != row['net']:
            raise ValidationError('Saved Stripe activity does not balance in exact cents.')
        kind = row['kind']
        if kind in PAYMENTS: totals['stripe_payments'] += row['amount']
        elif kind in REFUNDS: totals['stripe_refunds'] += row['amount']
        elif kind in COSTS: totals['standalone_costs'] -= row['amount']
        elif kind in TRANSFERS: totals['payout_movements'] += row['amount']
        else: totals['other_movements'] += row['amount']
        totals['stripe_fees'] += row['fee']
        totals['stripe_balance_movement'] += row['net']
        if row['availability'] == 'pending': totals['pending_net'] += row['net']
    totals['stripe_costs'] = totals['stripe_fees'] + totals['standalone_costs']
    totals['net_before_payouts'] = totals['stripe_payments'] + totals['stripe_refunds'] - totals['stripe_costs']
    assert totals['net_before_payouts'] + totals['other_movements'] + totals['payout_movements'] == totals['stripe_balance_movement']
    if any(row['kind'] not in KNOWN for row in rows):
        issues.append('Other Stripe balance movements need review. They have not been classified as sales or fees.')
    native = {row.provider_id: row for row in NativeBalanceEntry.objects.filter(occurred_at__gte=first, occurred_at__lt=last)
        .select_related('finance__attempt__invoice').prefetch_related('finance__attempt__invoice__entries','finance__attempt__adjustments','finance__attempt__refund_requests')}
    provider = {r['provider_id']: r for r in rows}
    for key, entry in native.items():
        match = provider.get(key)
        if not (source and match and entry.account_id == source.account_id and entry.currency == 'usd'
                and entry.source_id == match['source_id'] and entry.provider_type == match['kind']
                and (entry.amount,entry.fee,entry.net) == (match['amount'],match['fee'],match['net']) and finance_current(entry.finance)):
            issues.append('An app invoice transaction is missing, changed or awaiting fee reconciliation: ' + key)
    attempts = list(NativePaymentAttempt.objects.filter(ledger_entry__date__gte=start, ledger_entry__date__lte=cutoff)
        .select_related('invoice','finance').prefetch_related('invoice__entries','adjustments','refund_requests'))
    for attempt in attempts:
        finance = getattr(attempt,'finance',None)
        if not finance or not finance_current(finance):
            issues.append('A confirmed app payment needs fee reconciliation: ' + attempt.invoice.number)
    for row in rows:
        entry = native.get(row['provider_id'])
        row['invoice_id'] = str(entry.finance.attempt.invoice_id) if entry else ''
        row['invoice'] = entry.finance.attempt.invoice.number if entry else ''
    entries = list(NativeInvoiceEntry.objects.filter(date__gte=start,date__lte=cutoff)
        .select_related('invoice','reverses').order_by('date','created_at','id'))
    # Include reversed entries' provider links so a correction cannot masquerade as offline cash.
    all_ids = {e.pk for e in entries} | {e.reverses_id for e in entries if e.reverses_id}
    stripe_entry_ids = set(NativePaymentAttempt.objects.filter(ledger_entry_id__in=all_ids).values_list('ledger_entry_id',flat=True))
    ledger = []
    for entry in entries:
        amount = minor(entry.amount); kind = entry.kind
        online = entry.pk in stripe_entry_ids or entry.reverses_id in stripe_entry_ids or bool(entry.adjustment_id)
        if kind == 'payment' and not online: totals['offline_payments'] += amount
        elif kind == 'credit': totals['credits'] += amount
        elif kind == 'reversal' and entry.reverses.kind == 'payment' and not online: totals['offline_corrections'] -= amount
        elif kind == 'reversal' and entry.reverses.kind == 'credit': totals['credit_corrections'] -= amount
        ledger.append({'id':str(entry.pk),'invoice_id':str(entry.invoice_id),'invoice':entry.invoice.number,
            'date':entry.date.isoformat(),'kind':kind,'amount':amount,'method':entry.method,'online':online})
    receivables = []
    invoices = NativeInvoice.objects.filter(issue_date__lte=cutoff).prefetch_related('entries').order_by('number')
    for invoice in invoices:
        historical = [e for e in invoice.entries.all() if e.date <= cutoff]
        reversed_ids = {e.reverses_id for e in historical if e.kind == 'reversal'}
        paid = sum(minor(e.amount) * (-1 if e.kind in {'refund','return'} else 1) for e in historical
            if e.kind in {'payment','refund','return','recovery'} and e.pk not in reversed_ids)
        credits = sum(minor(e.amount) for e in historical if e.kind == 'credit' and e.pk not in reversed_ids)
        voided = invoice.voided_at and invoice.voided_at.astimezone(EASTERN).date() <= cutoff
        principal = 0 if voided else minor(invoice.amount)
        due = principal - paid - credits
        if start <= invoice.issue_date <= cutoff: totals['issued'] += principal
        totals['outstanding'] += max(due,0); totals['credit_balances'] += max(-due,0)
        overdue = due > 0 and invoice.due_date < cutoff
        if overdue: totals['overdue'] += due
        if due:
            receivables.append({'id':str(invoice.pk),'number':invoice.number,'client':invoice.snapshot.get('client_name',''),
                'project':invoice.snapshot.get('project_name',''),'issue_date':invoice.issue_date.isoformat(),
                'due_date':invoice.due_date.isoformat(),'amount':principal,'paid':paid,'credits':credits,'due':due,'overdue':overdue})
    if start <= timezone.now().astimezone(EASTERN).date() <= end:
        issues.append('This month is still in progress. Its figures are provisional; save a completed-month review after month end.')
    result = {'schema':1,'month':start.strftime('%Y-%m'),'through':cutoff.isoformat(),'totals':totals,
        'stripe':rows,'ledger':ledger,'receivables':receivables,'issues':sorted(set(issues)),
        'source':{'id':str(source.pk),'account':source.account_id,'captured_at':source.captured_at.isoformat()} if source else None,
        'as_of':timezone.now().isoformat()}
    result['fingerprint'] = report_fingerprint(result)
    return result


def report_fingerprint(data):
    value = {k:v for k,v in data.items() if k not in {'as_of','fingerprint','source'}}
    value['account'] = data['source']['account'] if data.get('source') else ''
    return digest(value)


@transaction.atomic
def save_review(*,month,token,fingerprint,note,confirm,actor):
    require_owner(actor); require_staff(actor,'workqueue.change_workitem'); lock_queue()
    start,_ = month_dates(month)
    if not isinstance(note,str) or not note.strip() or len(note.strip()) > 2000 or confirm is not True:
        raise ValidationError('Add a review note and confirm that you checked these figures.')
    old = NativeFinancialReview.objects.filter(pk=token).first()
    if old:
        if old.actor_id != actor.pk or old.month_id != start or old.report['fingerprint'] != fingerprint or old.note != note.strip():
            raise ValidationError('This review reference belongs to another submission.')
        return old
    data = build_report(month)
    if data['fingerprint'] != fingerprint:
        raise EditConflict('The month changed while you were reviewing it. Reload and check the new figures.')
    if data['issues']:
        raise ValidationError('Resolve the review items before saving a completed-month review.')
    source = NativeMonthSnapshot.objects.get(pk=data['source']['id'])
    state = source.month
    if not state.checked_at or timezone.now() - state.checked_at > timedelta(hours=1):
        raise ValidationError('Refresh Stripe activity within the last hour before saving a completed-month review.')
    return NativeFinancialReview.objects.create(id=token,month=state,source=source,report=data,
        checksum=digest(data),note=note.strip(),actor=actor)


def saved_report(review):
    if review.checksum != digest(review.report) or review.report.get('fingerprint') != report_fingerprint(review.report):
        raise ValidationError('This saved review failed its integrity check. Do not use its totals.')
    return copy.deepcopy(review.report)
