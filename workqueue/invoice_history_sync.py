"""Read-only FreshBooks reconciliation, with durable leases and immutable CSV facts."""
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from . import freshbooks
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink
from .invoice_history_models import HistoricalInvoice, HistoricalInvoiceBalance, InvoiceHistorySync
from .models import Client, ClientContact
from .services import lock_queue

ACTIVE = ('scanning', 'contacts')
MAX_PAGES = 100
SCAN_ERROR = 'FreshBooks could not be checked. Reconnect if needed, then request another check. Previous verified balances are retained.'


def selected_account():
    if not freshbooks.configured():
        raise ValidationError('FreshBooks is not configured. Connect it before checking invoice history.')
    try:
        return freshbooks.account_id()
    except freshbooks.BillingError:
        raise ValidationError('Reconnect FreshBooks and select the business before checking invoice history.') from None


def target_records(account):
    return {i.number: {'id': str(i.pk), 'fingerprint': i.fingerprint, 'issued': i.issued.isoformat(),
                      'currency': i.currency, 'provider_invoice_id': getattr(getattr(i, 'live_balance', None), 'provider_invoice_id', None)}
            for i in HistoricalInvoice.objects.filter(account_id=account, removed_at__isnull=True)
                .select_related('live_balance').defer('lines')}


def request_sync(actor=None, automatic=False):
    if actor:
        freshbooks.require_staff(actor, 'workqueue.change_workitem')
    account = selected_account()
    with transaction.atomic():
        lock_queue()
        # Serialize enqueue operations with business selection and other queue changes.
        connection = FreshBooksConnection.objects.select_for_update().get(pk=1)
        if connection.account_id != account or connection.needs_reconnect:
            raise ValidationError('The FreshBooks connection changed. Reload before checking.')
        active = InvoiceHistorySync.objects.filter(account_id=account, state__in=ACTIVE).first()
        if active:
            return active
        targets = target_records(account)
        if not targets:
            return None
        last = InvoiceHistorySync.objects.filter(account_id=account).order_by('-created_at', '-pk').first()
        if last:
            elapsed = timezone.now() - last.created_at
            # A manual request cannot repeatedly consume provider calls. Automatic failures back off.
            if elapsed < timedelta(minutes=5):
                return last
            if automatic:
                same_sources = {k: (v['id'], v['fingerprint']) for k, v in targets.items()} == {
                    k: (v['id'], v['fingerprint']) for k, v in last.targets.items()}
                delay = timedelta(minutes=30) if last.state == 'failed' else timedelta(hours=24)
                if same_sources and elapsed < delay:
                    return last
        return InvoiceHistorySync.objects.create(account_id=account, actor=actor, targets=targets)


def integer(value, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,12}', str(value)) or int(value) < minimum:
        raise ValueError('Invalid provider integer')
    return int(value)


def money(raw, currency):
    if not isinstance(raw, dict) or raw.get('code') != currency:
        raise ValueError('Inconsistent provider currency')
    try:
        amount = Decimal(str(raw['amount']))
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError('Invalid provider money') from None
    if not amount.is_finite() or abs(amount) > Decimal('999999999999.99') or amount != amount.quantize(Decimal('.01')):
        raise ValueError('Invalid provider precision')
    return str(amount)


def normalize(raw, account, target):
    if not isinstance(raw, dict):
        raise ValueError('Invalid invoice')
    for key in ('accountid', 'accounting_systemid'):
        if raw.get(key) != account:
            raise ValueError('Wrong provider business')
    invoice_id = integer(raw.get('invoiceid'), 1)
    if raw.get('id') is not None and integer(raw['id'], 1) != invoice_id:
        raise ValueError('Conflicting invoice IDs')
    if target['provider_invoice_id'] and target['provider_invoice_id'] != invoice_id:
        raise ValueError('Invoice identity changed')
    if raw.get('create_date') != target['issued'] or raw.get('currency_code') != target['currency']:
        raise ValueError('Invoice date or currency does not match export')
    status = raw.get('v3_status') or raw.get('display_status')
    if not isinstance(status, str) or not re.fullmatch(r'[a-zA-Z_-]{1,50}', status):
        raise ValueError('Invalid invoice status')
    visibility = integer(raw.get('vis_state'))
    if visibility not in (0, 2):
        raise ValueError('Invoice was deleted')
    due = raw.get('due_date')
    if due: due = date.fromisoformat(due).isoformat()
    currency = target['currency']
    return {'provider_invoice_id': invoice_id, 'customer_id': integer(raw.get('customerid'), 1), 'status': status.lower(),
            'currency': currency, 'amount': money(raw.get('amount'), currency), 'paid': money(raw.get('paid'), currency),
            'outstanding': money(raw.get('outstanding'), currency), 'due': due or None, 'archived': visibility == 2}


def read_page(job):
    result = freshbooks.api('GET', f'/accounting/account/{job.account_id}/invoices/invoices',
                           params={'page': job.page, 'per_page': 100}).get('response', {}).get('result', {})
    if not isinstance(result, dict) or not isinstance(result.get('invoices'), list):
        raise ValueError('Invalid invoice list')
    page, pages, total = (integer(result.get(k), 1 if k == 'page' else 0) for k in ('page', 'pages', 'total'))
    per_page = integer(result.get('per_page'), 1)
    rows = result['invoices']
    if page != job.page or per_page != 100 or pages > MAX_PAGES or len(rows) > 100:
        raise ValueError('Invalid pagination')
    if pages != max(1, (total+99)//100) and not (total == 0 and pages == 0 and page == 1):
        raise ValueError('Inconsistent pagination')
    if job.pages is not None and (job.pages != pages or job.total != total):
        raise ValueError('Invoice list changed during check')
    expected_count = min(100, max(0, total-(page-1)*100))
    if len(rows) != expected_count:
        raise ValueError('Incomplete invoice page')
    matches = dict(job.matches)
    for raw in rows:
        if not isinstance(raw, dict): raise ValueError('Invalid invoice row')
        number = raw.get('invoice_number')
        if number not in job.targets:
            continue
        if number in matches:
            # Duplicate numbers or a moving page boundary can never pick a winner.
            matches[number] = {'error': 'Multiple FreshBooks invoices matched this number. Review it in FreshBooks.'}
            continue
        try:
            matches[number] = normalize(raw, job.account_id, job.targets[number])
        except (ValueError, TypeError, KeyError, InvalidOperation):
            matches[number] = {'error': 'The invoice identity or amounts did not pass verification. Review it in FreshBooks.'}
    ids = [v['provider_invoice_id'] for v in matches.values() if 'provider_invoice_id' in v]
    duplicate_ids = {i for i in ids if ids.count(i) > 1}
    for number, value in matches.items():
        if value.get('provider_invoice_id') in duplicate_ids:
            matches[number] = {'error': 'Conflicting FreshBooks invoice identity. Review it in FreshBooks.'}
    return pages, total, matches


def apply_balances(job):
    """Called under the job lock, after all pages passed validation."""
    now = timezone.now()
    verified, review = 0, 0
    customers = set()
    for number, target in job.targets.items():
        invoice = HistoricalInvoice.objects.select_for_update().filter(pk=target['id'], account_id=job.account_id,
            number=number, fingerprint=target['fingerprint'], removed_at__isnull=True).first()
        if not invoice: continue
        balance, _ = HistoricalInvoiceBalance.objects.get_or_create(invoice=invoice)
        value = job.matches.get(number, {'error': 'Not found among active or archived FreshBooks invoices. Review the original record; it may have been deleted.'})
        balance.attempted_at = now
        if 'error' in value:
            balance.error = value['error']; review += 1
        elif balance.provider_invoice_id and balance.provider_invoice_id != value['provider_invoice_id']:
            balance.error = 'The linked invoice changed during this check. Request a new check.'; review += 1
        else:
            if balance.customer_id != value['customer_id']:
                balance.billing_name = balance.billing_email = ''; balance.contact_verified_at = None
            for key, val in value.items(): setattr(balance, key, val)
            balance.verified_at = now; balance.error = ''; verified += 1
            if not invoice.client_id: customers.add(balance.customer_id)
        balance.save()
    job.customers = sorted(customers)
    job.result = {'verified': verified, 'review': review, 'contacts_checked': 0, 'contact_errors': 0}
    job.state = 'contacts' if customers else 'complete'
    job.matches = {} # Persist only the normalized snapshot, not redundant staging data.
    if job.state == 'complete': job.finished_at = now


def read_contact(job):
    customer = job.customers[0]
    try:
        raw = freshbooks.api('GET', f'/accounting/account/{job.account_id}/users/clients/{customer}').get('response', {}).get('result', {}).get('client', {})
        if not isinstance(raw, dict) or integer(raw.get('id'), 1) != customer or raw.get('accounting_systemid') != job.account_id:
            raise ValueError('Wrong client identity')
        if integer(raw.get('vis_state')) not in (0, 2):
            raise ValueError('Deleted client')
        email = (raw.get('email') or '').strip().lower()
        if email: validate_email(email)
        name = (raw.get('organization') or ' '.join(v for v in (raw.get('fname'), raw.get('lname')) if v)).strip()
        if len(email) > 254 or len(name) > 200: raise ValueError('Invalid client details')
        return customer, name, email, True
    except (freshbooks.BillingError, ValidationError, ValueError, TypeError, AttributeError):
        return customer, '', '', False


def fail_job(job_id, lease):
    with transaction.atomic():
        lock_queue()
        job = InvoiceHistorySync.objects.select_for_update().filter(pk=job_id, lease=lease, state__in=ACTIVE).first()
        if not job: return
        job.state, job.error, job.finished_at = 'failed', SCAN_ERROR, timezone.now()
        job.lease = job.lease_until = None; job.matches = {}
        job.save()


def process_step():
    account = selected_account()
    now, lease = timezone.now(), uuid.uuid4()
    with transaction.atomic():
        lock_queue()
        # Obsolete-business scans cannot later apply or monopolize processing.
        InvoiceHistorySync.objects.filter(state__in=ACTIVE).exclude(account_id=account).update(
            state='failed', error='The selected FreshBooks business changed. Request a check for the current business.', finished_at=now, lease=None, lease_until=None)
        job = InvoiceHistorySync.objects.select_for_update().filter(account_id=account, state__in=ACTIVE).order_by('created_at', 'pk').first()
        if not job or (job.lease_until and job.lease_until > now): return False
        job.lease, job.lease_until = lease, now+timedelta(seconds=150)
        job.save(update_fields=['lease', 'lease_until'])
    # Network requests deliberately run outside queue/record locks.
    try:
        response = read_page(job) if job.state == 'scanning' else read_contact(job)
    except (freshbooks.BillingError, ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        fail_job(job.pk, lease); return True
    with transaction.atomic():
        lock_queue()
        connection = FreshBooksConnection.objects.select_for_update().get(pk=1)
        current = InvoiceHistorySync.objects.select_for_update().get(pk=job.pk)
        if current.lease != lease or current.state not in ACTIVE: return True
        if connection.account_id != account or connection.needs_reconnect:
            current.state, current.error, current.finished_at = 'failed', SCAN_ERROR, timezone.now()
            current.matches = {}
        elif current.state == 'scanning':
            current.pages, current.total, current.matches = response
            current.seen += min(100, max(0, current.total-(current.page-1)*100))
            if current.page >= current.pages:
                if current.seen != current.total: raise ValueError('Incomplete scan')
                apply_balances(current)
            else:
                current.page += 1
        else:
            customer, name, email, valid = response
            if valid:
                HistoricalInvoiceBalance.objects.filter(invoice__account_id=account, invoice__removed_at__isnull=True,
                    invoice__client__isnull=True, customer_id=customer, error='').update(
                    billing_name=name, billing_email=email, contact_verified_at=timezone.now())
                current.result['contacts_checked'] += 1
            else:
                current.result['contact_errors'] += 1
                HistoricalInvoiceBalance.objects.filter(invoice__account_id=account, invoice__client__isnull=True,
                    customer_id=customer).update(contact_verified_at=None)
            current.customers = current.customers[1:]
            if not current.customers:
                current.state, current.finished_at = 'complete', timezone.now()
        current.lease = current.lease_until = None
        current.save()
    return True


def sync_history(limit=4):
    try:
        request_sync(automatic=True)
        processed = 0
        for _ in range(limit):
            if not process_step(): break
            processed += 1
        return processed
    except ValidationError:
        # A disconnected/disabled provider cannot enqueue new scans or make calls.
        return 0


def live_context(invoice, connection=None):
    balance = getattr(invoice, 'live_balance', None)
    if not balance: return None
    connection = connection or FreshBooksConnection.objects.filter(pk=1).first()
    stale = not balance.verified_at or balance.verified_at < timezone.now()-timedelta(hours=24) or bool(balance.error) or not connection or connection.needs_reconnect or connection.account_id != invoice.account_id
    url = None
    if balance.provider_invoice_id and re.fullmatch(r'[A-Za-z0-9_-]{1,50}', invoice.account_id):
        url = f'https://my.freshbooks.com/#/invoice/{invoice.account_id}-{balance.provider_invoice_id}'
    return {'balance': balance, 'stale': stale, 'url': url}


def contact_suggestions(invoice):
    if invoice.client_id: return []
    balance = getattr(invoice, 'live_balance', None)
    if not balance or balance.error or not balance.contact_verified_at or balance.contact_verified_at < timezone.now()-timedelta(hours=24): return []
    ids = set(FreshBooksClientLink.objects.filter(account_id=invoice.account_id, customer_id=balance.customer_id).values_list('client_id', flat=True))
    if balance.billing_email:
        ids.update(ClientContact.objects.filter(email__iexact=balance.billing_email, archived_at__isnull=True).values_list('client_id', flat=True))
    # Resolve existing CRM merges; never infer associations from names or create contacts.
    resolved = set()
    for client in Client.objects.filter(pk__in=ids):
        seen = set()
        while client.merged_into_id and client.pk not in seen:
            seen.add(client.pk); client = client.merged_into
        if not client.merged_into_id: resolved.add(client.pk)
    return list(Client.objects.filter(pk__in=resolved).order_by('name', 'pk'))
