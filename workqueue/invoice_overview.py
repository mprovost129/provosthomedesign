"""Read-only billing snapshots. No provider calls or financial actions on reads."""
from datetime import timedelta
import re
from django.db.models import BooleanField, Case, CharField, Exists, OuterRef, Q, Value, When
from django.db.models.functions import Lower, Trim, Upper
from django.utils import timezone
from .freshbooks_models import FreshBooksInvoice
from .models import WorkItem, CLOSED_STATUSES


STATUS_LABELS = {
    'created': 'Created', 'draft': 'Draft', 'sent': 'Sent', 'viewed': 'Viewed',
    'paid': 'Paid', 'autopaid': 'Automatically paid', 'partial': 'Partially paid',
    'deposit-partial': 'Deposit partially paid', 'overdue': 'Overdue',
    'disputed': 'Disputed', 'resolved': 'Dispute resolved', 'failed': 'Payment failed',
    'retry': 'Payment retry pending', 'success': 'Automatic payment succeeded',
    'deleted': 'Deleted', 'voided': 'Voided',
}
BUCKET_LABELS = {'preparing': 'Preparing draft', 'draft': 'Draft',
    'outstanding': 'Outstanding', 'overdue': 'Overdue', 'paid': 'Paid',
    'review': 'Needs review', 'closed': 'Voided or deleted'}
RECEIVABLE_STATUSES = ('sent', 'viewed', 'partial', 'deposit-partial', 'overdue', 'resolved')


def invoice_queryset(search='', scope='all', now=None):
    now = now or timezone.now()
    jobs = WorkItem.objects.filter(project_id=OuterRef('proposal__project_id'))
    linked = Q(state='linked', invoice_id__isnull=False)
    valid_currency = Q(currency_code__regex=r'^[A-Z]{3}$')
    balances = Q(amount__isnull=False, paid__isnull=False, outstanding__isnull=False,
        amount__gte=0, paid__gte=0, outstanding__gte=0)
    issue = (Q(state__in=['failed', 'unknown']) | (Q(state='linked') & (
        Q(invoice_id__isnull=True) | ~Q(last_error='') | Q(last_sync_at__isnull=True) |
        ~valid_currency | ~balances | ~Q(remote_status__in=STATUS_LABELS) |
        (Q(remote_status__in=['paid', 'autopaid']) & ~Q(outstanding=0)))))
    pending = Q(state__in=['queued', 'creating'])
    closed = linked & Q(remote_status__in=['voided', 'deleted'])
    draft = linked & Q(remote_status__in=['draft', 'created'])
    paid = linked & Q(remote_status__in=['paid', 'autopaid'], outstanding=0)
    overdue = linked & Q(remote_status='overdue', outstanding__gt=0)
    receivable = linked & Q(remote_status__in=RECEIVABLE_STATUSES, outstanding__gt=0) & valid_currency & balances
    # Draft and closed balances are not receivables. Provider errors remain visible
    # with last-saved balances, explicitly identified as needing review.
    invoices = FreshBooksInvoice.objects.select_related('proposal__project', 'proposal__client', 'revision').annotate(
        remote_status=Lower(Trim('billing_status')), currency_code=Upper(Trim('currency')),
        active_project=Exists(jobs.exclude(status__in=CLOSED_STATUSES)),
    ).annotate(
        needs_review=Case(When(issue, then=Value(True)), default=Value(False), output_field=BooleanField()),
        stale=Case(When(linked & Q(last_sync_at__lt=now-timedelta(hours=24)), then=Value(True)),
            default=Value(False), output_field=BooleanField()),
        receivable=Case(When(receivable, then=Value(True)), default=Value(False), output_field=BooleanField()),
        billing_bucket=Case(
            When(pending, then=Value('preparing')),
            When(closed, then=Value('closed')),
            When(issue, then=Value('review')),
            When(draft, then=Value('draft')),
            When(paid, then=Value('paid')),
            When(overdue, then=Value('overdue')),
            When(receivable, then=Value('outstanding')),
            default=Value('review'), output_field=CharField()),
    ).annotate(
        attention=Case(When(Q(needs_review=True) | Q(stale=True) | Q(billing_bucket__in=['review', 'overdue']),
            then=Value(True)), default=Value(False), output_field=BooleanField()),
    )
    if scope == 'active':
        invoices = invoices.filter(active_project=True)
    if search:
        matching_jobs = jobs.filter(Q(reference__icontains=search) | Q(contact_email__icontains=search))
        invoices = invoices.alias(matching_job=Exists(matching_jobs)).filter(
            Q(invoice_number__icontains=search) | Q(proposal__number__icontains=search) |
            Q(proposal__title__icontains=search) | Q(proposal__project__name__icontains=search) |
            Q(proposal__project__street__icontains=search) | Q(proposal__client__name__icontains=search) |
            Q(revision__recipient__icontains=search) | Q(matching_job=True))
    return invoices


def invoice_row(invoice):
    return {'invoice': invoice, 'label': BUCKET_LABELS[invoice.billing_bucket],
        'remote_label': STATUS_LABELS.get(invoice.remote_status, 'Status unavailable'),
        'currency': invoice.currency_code if len(invoice.currency_code) == 3 and invoice.currency_code.isalpha() and invoice.currency_code.isascii() else 'Currency unavailable',
        'url': f'https://my.freshbooks.com/#/invoice/{invoice.account_id}-{invoice.invoice_id}'
            if invoice.invoice_id and re.fullmatch(r'[A-Za-z0-9]+', invoice.account_id) else '',
    }
