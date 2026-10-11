"""Read-only project budgets. Independent subqueries avoid multiplying time by jobs."""
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Count, Sum, Q, F, OuterRef, Subquery, Exists, Value, BigIntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import WorkProject, WorkItem, CLOSED_STATUSES, Status
from .time_models import TimeEntry
from .pm_models import Proposal
from .freshbooks_models import FreshBooksInvoice


def hours(seconds):
    return (Decimal(seconds) / 3600).quantize(Decimal('.01'), ROUND_HALF_UP)


def project_report_queryset(search=''):
    jobs = WorkItem.objects.filter(project_id=OuterRef('pk')).order_by().values('project_id')
    time = TimeEntry.objects.filter(project_id=OuterRef('pk'), deleted_at__isnull=True,
        duration_seconds__isnull=False).order_by().values('project_id')
    invoices = FreshBooksInvoice.objects.filter(proposal__project_id=OuterRef('pk'))
    review = invoices.filter(~Q(state='linked') | Q(last_sync_at__isnull=True)
        | Q(billing_status__iexact='draft') | Q(billing_status='')
        | Q(billing_status__iexact='deleted') | Q(billing_status__iexact='voided'))

    def metric(query, expression):
        return Coalesce(Subquery(query.annotate(metric=expression).values('metric')[:1],
            output_field=BigIntegerField()), Value(0), output_field=BigIntegerField())

    projects = WorkProject.objects.select_related('plan').annotate(
        job_count=metric(jobs, Count('pk')),
        active_count=metric(jobs, Count('pk', filter=~Q(status__in=CLOSED_STATUSES))),
        completed_count=metric(jobs, Count('pk', filter=Q(status=Status.COMPLETED))),
        seconds=metric(time, Sum('duration_seconds')),
        billable_seconds=metric(time, Sum('duration_seconds', filter=Q(billable=True))),
        revision_seconds=metric(time, Sum('duration_seconds', filter=Q(category='revision'))),
        has_invoice=Exists(invoices), invoice_review=Exists(review),
    ).filter(job_count__gt=0)
    if search:
        matching_jobs = WorkItem.objects.filter(project_id=OuterRef('pk')).filter(
            Q(reference__icontains=search) | Q(client_contact__client__name__icontains=search)
            | Q(client_contact__email__icontains=search))
        matching_proposals = Proposal.objects.filter(project_id=OuterRef('pk'), number__icontains=search)
        projects = projects.alias(matching_job=Exists(matching_jobs), matching_proposal=Exists(matching_proposals)).filter(
            Q(name__icontains=search) | Q(street__icontains=search) | Q(canonical_reference__icontains=search)
            | Q(matching_job=True) | Q(matching_proposal=True))
    return projects


OVER_ESTIMATE = Q(plan__estimated_hours__isnull=False, seconds__gt=F('plan__estimated_hours') * 3600)
COMPLETED = Q(active_count=0, completed_count__gt=0)
BILLING_REVIEW = COMPLETED & (Q(has_invoice=False) | Q(invoice_review=True))


def report_row(project, invoices, now=None):
    now = now or timezone.now()
    plan = getattr(project, 'plan', None)
    estimated = plan.estimated_hours if plan else None
    completed = project.active_count == 0 and project.completed_count > 0
    for invoice in invoices:
        invoice.snapshot_old = bool(invoice.last_sync_at and (now - invoice.last_sync_at).total_seconds() > 86400)
    return {
        'project': project, 'total': hours(project.seconds), 'billable': hours(project.billable_seconds),
        'nonbillable': hours(project.seconds - project.billable_seconds),
        'revisions': hours(project.revision_seconds), 'estimated': estimated,
        'over_estimate': estimated is not None and Decimal(project.seconds) > estimated * 3600,
        'remaining': None if estimated is None else (estimated - Decimal(project.seconds) / 3600).quantize(Decimal('.01'), ROUND_HALF_UP),
        'completed': completed, 'billing_review': completed and (not project.has_invoice or project.invoice_review),
        'invoices': invoices,
    }
