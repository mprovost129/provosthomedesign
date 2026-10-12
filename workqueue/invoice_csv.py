import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .crm import require_staff
from .crm_identity import normalized_text
from .freshbooks_models import FreshBooksConnection, FreshBooksInvoice
from .invoice_history_models import HistoricalInvoice, InvoiceCSVImport
from .models import Client, ClientContact, ClientEvent
from .services import EditConflict, lock_queue

HEADERS = ('Client Name', 'Invoice #', 'Date Issued', 'Date Due', 'Invoice Status', 'Date Paid',
    'Item Name', 'Item Description', 'Rate', 'Quantity', 'Discount Percentage', 'Line Subtotal',
    'Tax 1 Type', 'Tax 1 Amount', 'Tax 2 Type', 'Tax 2 Amount', 'Line Total', 'Currency')
META = ('Client Name', 'Date Issued', 'Date Due', 'Invoice Status', 'Date Paid', 'Currency')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def parse_export(upload):
    raw = upload.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValidationError('Use a FreshBooks invoice CSV smaller than 2 MB.')
    try:
        text = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(HEADERS):
            raise ValidationError('Use the FreshBooks invoice line-item export with its original 18 column headers.')
        groups = {}
        count = 0
        for source in reader:
            count += 1
            if count > 10000 or None in source or any(v is None or len(v) > 10000 or '\x00' in v for v in source.values()):
                raise ValidationError('The CSV has too many rows, incomplete columns, or an oversized/invalid cell.')
            metadata = {k: source[k].strip() for k in META}
            number = source['Invoice #'].strip()
            if not number or len(number) > 100 or not metadata['Client Name'] or len(metadata['Client Name']) > 200:
                raise ValidationError(f'Row {count+1}: enter an invoice number and client name within the supported limits.')
            if not re.fullmatch(r'[A-Z]{3}', metadata['Currency']) or not metadata['Invoice Status'] or len(metadata['Invoice Status']) > 50:
                raise ValidationError(f'Row {count+1}: currency or invoice status is invalid.')
            try:
                for key in ('Date Issued', 'Date Due', 'Date Paid'):
                    if metadata[key]: date.fromisoformat(metadata[key])
                if not metadata['Date Issued']: raise ValueError
                for key in ('Rate', 'Quantity', 'Discount Percentage', 'Line Subtotal', 'Tax 1 Amount', 'Tax 2 Amount', 'Line Total'):
                    value = Decimal(source[key])
                    if not value.is_finite() or abs(value) > Decimal('999999999999.99'): raise ValueError
                    if key in ('Line Subtotal', 'Tax 1 Amount', 'Tax 2 Amount', 'Line Total') and value != value.quantize(Decimal('.01')):
                        raise ValueError
            except (ValueError, InvalidOperation):
                raise ValidationError(f'Row {count+1}: check its dates and numeric values. No rows were imported.') from None
            record = groups.setdefault(number, {'number': number, 'metadata': metadata, 'lines': [], 'total': Decimal(0)})
            if record['metadata'] != metadata:
                raise ValidationError(f'Invoice {number} has conflicting client, date, status or currency values. Review the export.')
            record['lines'].append(source)
            record['total'] += Decimal(source['Line Total'])
        invoices = []
        if not groups: raise ValidationError('The CSV contains no invoice records.')
        for record in groups.values():
            if abs(record['total']) > Decimal('999999999999.99'):
                raise ValidationError('An invoice line total exceeds the supported amount.')
            record['total'] = str(record['total'].quantize(Decimal('.01')))
            # Line order is not invoice identity; repeated identical billable lines
            # remain repeated, and their totals are never silently deduplicated.
            canonical = {**record, 'lines': sorted(record['lines'], key=lambda row: json.dumps(row, sort_keys=True))}
            record['fingerprint'] = digest(canonical)
            invoices.append(record)
        return invoices, hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, csv.Error):
        raise ValidationError('The file must be a valid UTF-8 FreshBooks CSV.') from None


def preview_plan(batch):
    numbers = [i['number'] for i in batch.invoices]
    live = set(FreshBooksInvoice.objects.filter(account_id=batch.account_id, invoice_number__in=numbers).values_list('invoice_number', flat=True))
    history = {i.number: i for i in HistoricalInvoice.objects.filter(account_id=batch.account_id, number__in=numbers)}
    names = sorted({i['metadata']['Client Name'] for i in batch.invoices}, key=str.casefold)
    clients = list(Client.objects.filter(merged_into__isnull=True))
    exact = defaultdict(dict)
    suggestions = defaultdict(dict)
    for client in clients:
        exact[normalized_text(client.name)][str(client.pk)] = client
    for contact in ClientContact.objects.filter(archived_at__isnull=True, client__merged_into__isnull=True).select_related('client'):
        for name in (contact.full_name, contact.submitted_company):
            if normalized_text(name) not in ('', 'homeowner'):
                suggestions[normalized_text(name)][str(contact.client_id)] = contact.client
    mappings = []
    for name in names:
        key = normalized_text(name)
        candidates = {**suggestions[key], **exact[key]}
        entries = [{'id': str(c.pk), 'name': c.name, 'version': c.version} for c in sorted(candidates.values(), key=lambda c: c.name)]
        mappings.append({'name': name, 'candidates': entries,
            'recommended': next(iter(exact[key])) if len(exact[key]) == 1 else ''})
    invoices = []
    for record in batch.invoices:
        old = history.get(record['number'])
        action = ('linked' if record['number'] in live else 'removed' if old and old.removed_at else
            'duplicate' if old and old.fingerprint == record['fingerprint'] else 'conflict' if old else 'import')
        invoices.append({'number': record['number'], 'client_name': record['metadata']['Client Name'],
            'status': record['metadata']['Invoice Status'], 'currency': record['metadata']['Currency'],
            'total': record['total'], 'lines': len(record['lines']), 'action': action})
    return {'mappings': mappings, 'invoices': invoices}


@transaction.atomic
def apply_export(batch_id, plan_hash, choices, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    batch = InvoiceCSVImport.objects.select_for_update().get(pk=batch_id, actor=actor)
    if batch.applied_at: return batch.result
    if not FreshBooksConnection.objects.filter(pk=1, account_id=batch.account_id).exists():
        raise EditConflict('The selected FreshBooks business changed. Prepare a new preview.')
    plan = preview_plan(batch)
    if digest(plan) != plan_hash:
        raise EditConflict('Invoice records or client matches changed. Review the refreshed preview before importing.')
    clients = {}
    for index, mapping in enumerate(plan['mappings']):
        choice = choices.get(str(index), '')
        if choice and choice not in {c['id'] for c in mapping['candidates']}:
            raise ValidationError('Choose one of the reviewed client matches or leave it unassigned.')
        clients[mapping['name']] = Client.objects.get(pk=choice) if choice else None
    result = dict(imported=0, linked=0, duplicate=0, conflict=0, removed=0, matched=0, unassigned=0)
    associated = defaultdict(int)
    for record, proposed in zip(batch.invoices, plan['invoices']):
        if proposed['action'] != 'import':
            result[proposed['action']] += 1
            continue
        meta = record['metadata']
        client = clients[meta['Client Name']]
        HistoricalInvoice.objects.create(batch=batch, account_id=batch.account_id, number=record['number'],
            exported_client=meta['Client Name'], issued=meta['Date Issued'], due=meta['Date Due'] or None,
            paid_date=meta['Date Paid'] or None, exported_status=meta['Invoice Status'], currency=meta['Currency'],
            line_total=record['total'], lines=record['lines'], fingerprint=record['fingerprint'], client=client)
        result['imported'] += 1
        result['matched' if client else 'unassigned'] += 1
        if client: associated[client.pk] += 1
    batch.applied_at, batch.result = timezone.now(), result
    batch.save(update_fields=['applied_at', 'result'])
    for client_id, count in associated.items():
        Client.objects.filter(pk=client_id).update(version=F('version')+1, updated_at=timezone.now())
        ClientEvent.objects.create(client_id=client_id, actor=actor, action='Invoice history imported', changes={'batch': str(batch.pk), 'invoices': count})
    return result


@transaction.atomic
def change_batch_visibility(batch_id, actor, remove):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    batch = InvoiceCSVImport.objects.select_for_update().get(pk=batch_id, actor=actor)
    if not batch.applied_at: raise ValidationError('This import has not been applied.')
    if remove == bool(batch.removed_at): return batch
    when = timezone.now() if remove else None
    batch.records.update(removed_at=when)
    batch.removed_at = when
    batch.save(update_fields=['removed_at'])
    return batch
