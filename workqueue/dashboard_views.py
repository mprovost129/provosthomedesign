"""Staff landing page built from saved records; GET never contacts providers."""
from django.conf import settings
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.db.models.functions import Coalesce, Greatest, Lower, Trim
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .attention import attention_conditions, with_email_attention
from .freshbooks_models import FreshBooksConnection, FreshBooksInvoice
from .health import health_snapshot
from .invoice_history_models import HistoricalInvoice
from .invoice_history_sync import live_context
from .invoice_overview import invoice_queryset, invoice_row
from .models import Appointment, Client, ClientFollowUp, CLOSED_STATUSES, Status, WorkItem, WorkProject
from .pm_models import ProjectTask
from .pm_views import staff_view
from .proposal_overview import overview_queryset, LABELS as PROPOSAL_LABELS


RECENT_LIMIT = 6


def recent_invoices(now):
    """Show local and imported records once, preferring the connected invoice.

    Account + provider ID or account + normalized invoice number identifies a
    duplicate. No cross-business matching, exported totals or provider writes.
    """
    linked = invoice_queryset(now=now)
    current = list(linked.order_by('-created_at', '-pk')[:RECENT_LIMIT])
    matching = FreshBooksInvoice.objects.filter(account_id=OuterRef('account_id')).annotate(
        number_key=Lower(Trim('invoice_number'))).filter(
            Q(number_key=OuterRef('number_key')) & ~Q(number_key='')
            | Q(invoice_id=OuterRef('live_balance__provider_invoice_id'), invoice_id__isnull=False))
    history = HistoricalInvoice.objects.filter(removed_at__isnull=True).select_related(
        'client', 'project', 'live_balance').defer('lines').annotate(number_key=Lower(Trim('number'))).alias(
            already_linked=Exists(matching)).filter(already_linked=False).order_by('-issued', '-number', '-pk')
    connection = FreshBooksConnection.objects.filter(pk=1).only('account_id', 'needs_reconnect').first()
    rows = []
    for invoice in current:
        row = invoice_row(invoice)
        rows.append({
            'sort_date': timezone.localdate(invoice.created_at), 'key': str(invoice.pk),
            'number': invoice.invoice_number or 'Preparing invoice', 'client': row['client'],
            'client_name': row['client'].name if row['client'] else 'Client not linked',
            'project': row['project'], 'url': row['billing_url'], 'label': row['label'],
            'source': 'Connected invoice', 'date': timezone.localdate(invoice.created_at),
            'date_label': 'Created here', 'checked': invoice.last_sync_at,
            'stale': invoice.stale or invoice.needs_review, 'currency': row['currency'],
            'outstanding': invoice.outstanding if invoice.receivable else None,
        })
    for invoice in history[:RECENT_LIMIT]:
        live = live_context(invoice, connection)
        rows.append({
            'sort_date': invoice.issued, 'key': str(invoice.pk), 'number': invoice.number,
            'client': invoice.client, 'client_name': invoice.client.name if invoice.client_id else invoice.exported_client,
            'project': invoice.project, 'url': reverse('workqueue:invoice_history_detail', args=[invoice.pk]),
            'label': live['balance'].status if live and live['balance'].status else invoice.exported_status,
            'source': 'Imported history', 'date': invoice.issued, 'date_label': 'Issued',
            'checked': live['balance'].verified_at if live else None, 'stale': not live or live['stale'],
            'currency': '', 'outstanding': None,
        })
    return sorted(rows, key=lambda row: (row['sort_date'], row['key']), reverse=True)[:RECENT_LIMIT]


@staff_view
@require_GET
def dashboard(request):
    now, today = timezone.now(), timezone.localdate()
    active = WorkItem.objects.active()
    counts = dict(active.order_by().values_list('status').annotate(total=Count('pk')))
    active_total = sum(counts.values())
    status_rows = [{'value': value, 'label': label, 'count': counts.get(value, 0)}
                   for value, label in Status.choices if value not in CLOSED_STATUSES]
    queue_attention = with_email_attention(WorkItem.objects.all())
    conditions = attention_conditions()
    totals = queue_attention.aggregate(**{key: Count('pk', filter=condition)
        for key, (_label, condition) in conditions.items()})
    attention = [{'label': label, 'count': totals[key],
        'url': reverse('workqueue:queue') + '?scope=all&attention=' + key}
        for key, (label, _condition) in conditions.items() if totals[key]]
    visible = Q(work_items__id__isnull=False, work_items__deleted_at__isnull=True)
    projects = WorkProject.objects.annotate(
        job_count=Count('work_items', filter=visible, distinct=True),
        active_count=Count('work_items', filter=visible & ~Q(work_items__status__in=CLOSED_STATUSES), distinct=True),
        last_work=Max('work_items__updated_at', filter=visible),
    ).filter(job_count__gt=0).annotate(last_activity=Greatest(
        'created_at', Coalesce('last_work', 'created_at'), Coalesce('plan__updated_at', 'created_at')))
    clients = Client.objects.filter(merged_into__isnull=True).annotate(
        active_count=Count('contacts__work_items', filter=Q(contacts__work_items__deleted_at__isnull=True)
            & ~Q(contacts__work_items__status__in=CLOSED_STATUSES), distinct=True),
        last_work=Max('contacts__work_items__updated_at', filter=Q(contacts__work_items__deleted_at__isnull=True)),
    ).annotate(last_activity=Greatest('updated_at', Coalesce('last_work', 'updated_at')))
    due_clients = ClientFollowUp.objects.filter(completed_at__isnull=True, canceled_at__isnull=True,
        due_date__lte=today, client__merged_into__isnull=True).count()
    active_project_jobs = active.filter(project_id=OuterRef('project_id'))
    tasks = ProjectTask.objects.filter(completed_at__isnull=True, canceled_at__isnull=True).alias(
        has_active_work=Exists(active_project_jobs)).filter(has_active_work=True)
    if due_clients:
        attention.append({'label': 'Client follow-ups due', 'count': due_clients,
                          'url': reverse('workqueue:clients') + '?attention=due'})
    for key, label, condition in [('overdue', 'Overdue project tasks', Q(due_date__lt=today)),
                                  ('today', 'Project tasks due today', Q(due_date=today))]:
        count = tasks.filter(condition).count()
        if count:
            attention.append({'label': label, 'count': count,
                'url': reverse('workqueue:task_list') + '?scope=active&view=' + key})
    proposals = overview_queryset(now=now).defer(
        'draft', 'current_revision__snapshot')
    pending_proposals = proposals.filter(overview_state='awaiting').count()
    changes = proposals.filter(overview_state='changes').count()
    if changes:
        attention.append({'label': 'Proposal changes requested', 'count': changes,
                          'url': reverse('workqueue:proposal_overview') + '?state=changes'})
    invoices = invoice_queryset(now=now)
    invoice_summary = invoices.aggregate(total=Count('pk'),
        overdue=Count('pk', filter=Q(billing_bucket='overdue')),
        draft=Count('pk', filter=Q(billing_bucket='draft')),
        review=Count('pk', filter=Q(attention=True)))
    if invoice_summary['review']:
        attention.append({'label': 'Connected invoices to review', 'count': invoice_summary['review'],
                          'url': reverse('workqueue:invoice_overview') + '?state=attention'})
    history = HistoricalInvoice.objects.filter(removed_at__isnull=True)
    history_due = history.filter(live_balance__outstanding__gt=0,
        live_balance__status__in=['sent', 'viewed', 'partial', 'overdue', 'deposit-paid', 'deposit-partial']).count()
    if history_due:
        attention.append({'label': 'Imported invoices with saved balances due', 'count': history_due,
                          'url': reverse('workqueue:invoice_history') + '?live=due'})
    booking_enabled = getattr(settings, 'BOOKING_ENABLED', False)
    appointments = Appointment.objects.filter(ends_at__gt=now, cancel_requested=False,
        state__in=['pending', 'syncing', 'confirmed', 'attention']).select_related('project').order_by('starts_at', 'pk')[:5]
    return render(request, 'workqueue/dashboard.html', {
        'as_of': now, 'today': today, 'active_total': active_total,
        'project_total': projects.filter(active_count__gt=0).count(), 'client_total': clients.count(),
        'pending_proposals': pending_proposals, 'status_rows': status_rows, 'attention': attention,
        'recent_requests': WorkItem.objects.select_related('client_contact__client', 'project').order_by('-received_at', '-pk')[:RECENT_LIMIT],
        'recent_projects': projects.select_related('filing_folder').order_by('-last_activity', '-pk')[:RECENT_LIMIT],
        'recent_clients': clients.select_related('primary_contact').order_by('-last_activity', '-pk')[:RECENT_LIMIT],
        'recent_proposals': [{'proposal': p, 'label': PROPOSAL_LABELS[p.overview_state]}
                            for p in proposals.order_by('-updated_at', '-pk')[:5]],
        'recent_invoices': recent_invoices(now), 'invoice_summary': invoice_summary,
        'history_count': history.count(),
        'appointments': appointments if booking_enabled else [], 'show_appointments': booking_enabled,
        'system_health': health_snapshot() if getattr(settings, 'WORK_INTAKE_ENABLED', False) else None,
        'can_add_client': request.user.has_perm('workqueue.add_client'),
    })
