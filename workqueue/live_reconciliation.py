"""Exact-cents comparison of FreshBooks Payments CSV evidence with saved sources.

No provider calls, credential changes, payments, invoices, mail or ledger writes.
The CSV is independent evidence for fees that the payment API does not supply.
"""
import csv
import hashlib
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PureWindowsPath
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .billing_mail import require_owner
from .crm import require_staff
from .freshbooks_mirror import selected
from .freshbooks_mirror_data import signature
from .freshbooks_mirror_models import FreshBooksMirrorJob, FreshBooksMirrorRecord
from .live_reconciliation_models import FreshBooksPaymentExport, FreshBooksPaymentCheck
from .services import lock_queue

HEADERS = ['Source', 'Source Number', 'Source Type', 'Transaction Date', 'Transaction ID',
           'Gateway', 'Fees Paid', 'Amount', 'Currency', 'Method', 'Status']
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
EASTERN = ZoneInfo('America/New_York')
ONLINE_METHODS = {'visa', 'mastercard', 'americanexpress', 'amex', 'discover', 'dinersclub',
                  'jcb', 'unionpay', 'creditcard', 'debitcard', 'ach', 'banktransfer', 'paypal'}


def month_start(value):
    if not isinstance(value, str) or not re.fullmatch(r'20[0-9]{2}-(0[1-9]|1[0-2])', value):
        raise ValidationError('Choose a month in YYYY-MM format.')
    return date.fromisoformat(value + '-01')


def cents(value):
    # Three trailing decimal places are present in the real export. Accept exact
    # cents such as 20.600, never round away meaningful sub-cent amounts.
    if not isinstance(value, str) or not re.fullmatch(r'-?[0-9]{1,12}(?:\.[0-9]{1,8})?', value):
        raise ValueError('Invalid money')
    try:
        result = Decimal(value)
        if not result.is_finite() or result != result.quantize(Decimal('.01')):
            raise ValueError('Sub-cent amount')
        return int(result * 100)
    except InvalidOperation:
        raise ValueError('Invalid money') from None


def amount(value):
    return f'{Decimal(value) / 100:.2f}'


def parse_export(content, month):
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise ValidationError('Choose a nonempty FreshBooks transaction CSV no larger than 2 MB.')
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')), strict=True)
        if reader.fieldnames != HEADERS:
            raise ValueError('Wrong columns')
        rows = []; seen = set(); outside = 0
        for index, raw in enumerate(reader, 1):
            if index > MAX_ROWS or set(raw) != set(HEADERS) or any(v is None or len(v) > 1000 for v in raw.values()):
                raise ValueError('Invalid row')
            identifier = raw['Transaction ID'].strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', identifier) or identifier in seen:
                raise ValueError('Missing or repeated transaction ID')
            seen.add(identifier)
            stamp = datetime.fromisoformat(raw['Transaction Date'])
            if stamp.utcoffset() is None:
                raise ValueError('Timestamp needs an offset')
            local_day = stamp.astimezone(EASTERN).date()
            code = raw['Currency'].strip()
            if not re.fullmatch('[A-Z]{3}', code):
                raise ValueError('Invalid currency')
            gross = cents(raw['Amount'].strip())
            fee = cents(raw['Fees Paid'].strip()) if raw['Fees Paid'].strip() not in ('', '-') else None
            if gross < 0 or fee is not None and (fee < 0 or fee > gross):
                raise ValueError('Review signed or excessive fees separately')
            if local_day.replace(day=1) != month:
                outside += 1
                continue
            rows.append({'id': identifier, 'date': local_day.isoformat(), 'timestamp': stamp.isoformat(),
                'utc_date': stamp.astimezone(ZoneInfo('UTC')).date().isoformat(),
                'invoice': raw['Source Number'].strip(), 'source_type': raw['Source Type'].strip(),
                'client': raw['Source'].strip(), 'gateway': raw['Gateway'].strip(),
                'method': raw['Method'].strip(), 'status': raw['Status'].strip().lower(),
                'currency': code, 'gross': gross, 'fee': fee})
    except (ValueError, UnicodeError, csv.Error, OverflowError, TypeError):
        raise ValidationError('The transaction CSV has unsupported columns, invalid amounts, dates or duplicate IDs. Export it again from FreshBooks Online Payments; do not edit its values.') from None
    if not rows:
        raise ValidationError('No transactions fall in this month in Eastern time. An empty export cannot establish a verified zero.')
    return {'schema': 1, 'timezone': 'America/New_York', 'outside_period': outside, 'rows': rows}


def _key(invoice, day, code, gross):
    return invoice, day, code, gross


def compare(source, invoices, payments):
    """Pure comparison. Inputs are bounded, integrity-checked, frozen source facts."""
    invoice_ids = {r['provider_id']: r for r in invoices}
    by_key = defaultdict(list)
    active = []
    for payment in payments:
        if payment['visibility'] == 1 or payment['from_credit']:
            continue
        active.append(payment)
        invoice = invoice_ids.get(payment['provider_invoice_id'])
        if invoice and invoice['visibility'] != 1:
            by_key[_key(invoice['title'], payment['date'], payment['currency'], cents(payment['amount']))].append(payment)
    result_rows = []; totals = {}; used = set(); unresolved = []
    for row in source['rows']:
        row = dict(row); issues = []; matches = []
        key = _key(row['invoice'], row['date'], row['currency'], row['gross'])
        if row['gateway'].casefold() != 'freshbooks payments':
            issues.append('Different gateway; review separately.')
        if row['status'] == 'succeeded':
            matches = by_key.get(key, [])
            # Identical partial payments are not safely attributable without a
            # provider payment ID. Never arbitrarily consume a matching row.
            if len(matches) != 1:
                issues.append('No unique saved invoice/payment match.' if not matches else 'Multiple saved payments match; attribution needs review.')
            elif matches[0]['provider_id'] in used:
                issues.append('Another exported transaction already matched this saved payment.')
            else:
                used.add(matches[0]['provider_id'])
                row['payment_id'] = matches[0]['provider_id']
                row['invoice_id'] = matches[0]['provider_invoice_id']
                invoice = invoice_ids[row['invoice_id']]
                if (invoice['currency'] != row['currency'] or invoice['customer_id'] != matches[0]['customer_id']):
                    issues.append('Invoice currency or billing identity differs.')
                if cents(invoice['paid']) + cents(invoice['outstanding']) != cents(invoice['amount']):
                    issues.append('Current invoice paid plus outstanding differs from its total.')
            if row['fee'] is None:
                issues.append('Actual processing fee is missing.')
            if row['source_type'].casefold() != 'invoice':
                issues.append('Checkout-link or non-invoice source needs separate reconciliation.')
        elif row['status'] not in ('declined', 'failed'):
            issues.append('Pending, refunded, disputed or unfamiliar status: amounts need separate evidence.')
        if row['date'] != row['utc_date']:
            issues.append('Transaction crosses a UTC/Eastern date boundary; confirm its accounting date.')
        row['issues'] = issues
        row['net'] = row['gross'] - row['fee'] if row['status'] == 'succeeded' and row['fee'] is not None else None
        row['matched'] = not issues and row['status'] == 'succeeded'
        result_rows.append(row)
        total = totals.setdefault(row['currency'], {'currency': row['currency'], 'gross': 0, 'known_fees': 0,
            'net': 0, 'successful': 0, 'matched': 0, 'excluded': 0, 'unknown_fees': 0, 'review': 0})
        if row['status'] == 'succeeded':
            total['gross'] += row['gross']; total['successful'] += 1
            total['matched'] += int(row['matched'])
            if row['fee'] is not None:
                total['known_fees'] += row['fee']; total['net'] += row['net']
            else:
                total['unknown_fees'] += 1
        else:
            total['excluded'] += 1
        total['review'] += int(bool(issues))
    for payment in active:
        if payment['provider_id'] not in used:
            method = re.sub('[^a-z0-9]', '', payment['method'].casefold())
            unresolved.append({'provider_id': payment['provider_id'], 'invoice_id': payment['provider_invoice_id'],
                'date': payment['date'], 'currency': payment['currency'], 'gross': cents(payment['amount']),
                'method': payment['method'], 'possible_online': method in ONLINE_METHODS or bool(payment['gateway'])})
    for total in totals.values():
        # Never display a full net result when a successful fee is missing or a
        # refund/pending/dispute could change the period's result.
        total['unmatched_online'] = sum(r['possible_online'] and r['currency'] == total['currency'] for r in unresolved)
        total['net_complete'] = not total['unknown_fees'] and not total['review'] and not total['unmatched_online']
    return {'schema': 1, 'rows': result_rows, 'totals': list(totals.values()), 'other_payments': unresolved,
        'possible_missing_online': sum(r['possible_online'] for r in unresolved),
        'matched': sum(r['matched'] for r in result_rows), 'review': sum(bool(r['issues']) for r in result_rows)}


@transaction.atomic
def import_export(*, actor, content, filename, month):
    require_owner(actor); require_staff(actor, 'workqueue.change_workitem'); lock_queue()
    connection = selected()
    if not connection or not connection.business_id:
        raise ValidationError('Select your FreshBooks business first.')
    start = month_start(month); source = parse_export(content, start)
    return FreshBooksPaymentExport.objects.get_or_create(account_id=connection.account_id, month=start,
        file_hash=hashlib.sha256(content).hexdigest(), defaults={'business_id': connection.business_id,
            'business_name': connection.business_name, 'filename': PureWindowsPath(filename).name[:150],
            'source': source, 'source_hash': signature(source), 'created_by': actor})[0]


def _frozen(kind, account_id, business_id):
    job = FreshBooksMirrorJob.objects.filter(account_id=account_id, kind=kind).first()
    if not job or job.business_id != business_id or job.state != 'complete' or not job.complete_at:
        raise ValidationError('Wait for complete invoice and payment mirror scans, then compare again. Saved comparisons remain available.')
    if job.complete_at < timezone.now() - timedelta(hours=24):
        raise ValidationError('The mirror is over 24 hours old. Sync FreshBooks before comparing.')
    rows = list(FreshBooksMirrorRecord.objects.filter(account_id=account_id, kind=kind, missing=False).order_by('provider_id'))
    if (len(rows) != job.expected_total or len(rows) > 20000 or
            len(set(job.seen_ids)) != len(rows) or set(job.seen_ids) != {r.provider_id for r in rows}):
        raise ValidationError('Mirror source coverage is incomplete. Complete a new sync before comparing.')
    facts = []; references = []
    for record in rows:
        if (record.business_id != business_id or record.last_seen_generation != job.generation or
                signature(record.source) != record.source_hash or record.source['provider_id'] != record.provider_id):
            raise ValidationError('Saved source integrity or scan coverage needs review. Sync FreshBooks before comparing.')
        keys = (['provider_id', 'title', 'customer_id', 'currency', 'amount', 'paid', 'outstanding', 'visibility'] if kind == 'invoices'
                else ['provider_id', 'provider_invoice_id', 'customer_id', 'date', 'currency', 'amount', 'method', 'gateway', 'visibility', 'from_credit'])
        facts.append({k: record.source[k] for k in keys})
        references.append({'id': str(record.pk), 'provider_id': record.provider_id, 'revision': record.revision, 'hash': record.source_hash})
    return facts, {'generation': str(job.generation), 'complete_at': job.complete_at.isoformat(), 'records': references}


@transaction.atomic
def create_check(*, actor, evidence_id):
    require_owner(actor); require_staff(actor, 'workqueue.change_workitem'); lock_queue()
    connection = selected()
    evidence = FreshBooksPaymentExport.objects.select_for_update().get(pk=evidence_id)
    if not connection or (evidence.account_id, evidence.business_id) != (connection.account_id, connection.business_id):
        raise ValidationError('This evidence belongs to a different FreshBooks business.')
    if signature(evidence.source) != evidence.source_hash:
        raise ValidationError('Saved transaction evidence integrity needs review. Financial totals are unavailable.')
    try:
        invoices, invoice_scan = _frozen('invoices', evidence.account_id, evidence.business_id)
        payments, payment_scan = _frozen('payments', evidence.account_id, evidence.business_id)
        payments = [r for r in payments if date.fromisoformat(r['date']).replace(day=1) == evidence.month]
        result = compare(evidence.source, invoices, payments)
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise ValidationError('Saved source values need review. No financial result was saved.') from None
    # All saved evidence is bounded and allowlisted. Retain the input facts and
    # versions so later synchronization cannot silently rewrite a prior check.
    result.update(invoices=invoices, payments=payments, invoice_scan=invoice_scan, payment_scan=payment_scan,
                  evidence_hash=evidence.source_hash)
    stamp = signature(result)
    previous = evidence.checks.first()
    if previous and previous.result_hash == stamp:
        return previous
    return FreshBooksPaymentCheck.objects.create(evidence=evidence, result=result, result_hash=stamp, created_by=actor)
