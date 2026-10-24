"""Current, staff-only statement projection of the trial ledger. No provider writes."""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .crm import require_staff
from .document_content import client_projection, issuer
from .models import Client
from .native_invoice_models import NativeInvoice
from .native_invoices import balance
from .pm_services import current_client
from .services import lock_queue

ZERO = Decimal('0.00')


@transaction.atomic
def statement_for(client, *, actor):
    require_staff(actor, 'workqueue.view_workitem')
    # All supported ledger edits and CRM merges use this same lock. Materialize
    # the complete safe projection before releasing it; PDF rendering is outside.
    lock_queue()
    client = current_client(Client.objects.get(pk=client.pk))
    client_ids = {client.pk}
    frontier = client_ids
    while frontier:
        frontier = set(Client.objects.filter(merged_into_id__in=frontier).values_list('pk', flat=True)) - client_ids
        client_ids.update(frontier)
    invoices = list(NativeInvoice.objects.filter(plan__document__client_id__in=client_ids)
                    .select_related('plan__document').prefetch_related('entries').order_by('issue_date', 'number'))
    now = timezone.now()
    today = timezone.localdate(now)
    rows, activity = [], []
    aging = [ZERO for _ in range(5)]
    total, paid, credited, voided = ZERO, ZERO, ZERO, ZERO
    for invoice in invoices:
        amounts = balance(invoice)
        row = {'id': invoice.pk, 'number': invoice.number, 'project': invoice.snapshot.get('project_name', ''),
               'billed_to': invoice.snapshot.get('client_name', ''), 'issue_date': invoice.issue_date,
               'due_date': invoice.due_date, 'amount': invoice.amount, **amounts}
        row.pop('reversed_ids')
        rows.append(row)
        total += invoice.amount
        paid += amounts['paid']; credited += amounts['credited']
        days = (today - invoice.due_date).days
        bucket = 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4
        aging[bucket] += amounts['due']
        def add(day, timestamp, rank, kind, amount, reference=''):
            activity.append({'date': day, '_sort': (day, timestamp, rank, invoice.number, reference),
                             'kind': kind, 'number': invoice.number, 'invoice_id': invoice.pk,
                             'project': row['project'], 'amount': amount,
                             'amount_text': ('-' if amount < 0 else '')+f'${abs(amount):,.2f}'})
        add(invoice.issue_date, invoice.created_at, 0, 'Invoice', invoice.amount)
        entries = list(invoice.entries.all())
        by_id = {entry.pk: entry for entry in entries}
        for entry in entries:
            kind = entry.get_kind_display()
            if entry.kind == 'reversal':
                original = by_id[entry.reverses_id]
                kind = 'Payment reversal' if original.kind == 'payment' else 'Credit reversal'
            add(entry.date, entry.created_at, 1, kind, entry.amount if entry.kind == 'reversal' else -entry.amount, str(entry.pk))
        if invoice.voided_at:
            voided += invoice.amount
            add(timezone.localdate(invoice.voided_at), invoice.voided_at, 2, 'Invoice void', -invoice.amount)
    running = ZERO
    activity.sort(key=lambda row: row['_sort'])
    for row in activity:
        running += row['amount']
        row['running_balance'] = running
        del row['_sort']
    due = sum((row['due'] for row in rows), ZERO)
    if running != due or total - voided - paid - credited != due:
        raise ValueError('The statement does not reconcile with the trial ledger.')
    return {'client_id': client.pk, 'client': client_projection(client), 'issuer': issuer(), 'generated_at': now,
            'invoices': rows, 'activity': activity,
            'total': total, 'net_invoiced': total-voided, 'paid': paid, 'credited': credited, 'voided': voided, 'due': due,
            'open_invoices': [row for row in rows if row['due'] > 0],
            'aging': list(zip(['Not overdue', '1-30 days', '31-60 days', '61-90 days', 'Over 90 days'], aging))}
