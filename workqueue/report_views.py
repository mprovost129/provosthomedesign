from collections import defaultdict
from django.core.paginator import Paginator
from django.db.models import Count, Sum, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .pm_views import staff_view
from .freshbooks_models import FreshBooksInvoice
from .reporting import project_report_queryset, report_row, hours, OVER_ESTIMATE, BILLING_REVIEW, COMPLETED


@staff_view
@require_GET
def project_reports(request):
    q = request.GET.get('q', '').strip()[:200]
    scope = request.GET.get('scope', 'active')
    if scope not in ('active', 'all', 'completed'):
        scope = 'active'
    attention = request.GET.get('attention', 'all')
    if attention not in ('all', 'over', 'billing', 'missing'):
        attention = 'all'
    projects = project_report_queryset(q)
    if scope == 'active':
        projects = projects.filter(active_count__gt=0)
    elif scope == 'completed':
        projects = projects.filter(COMPLETED)
    filters = {'over': OVER_ESTIMATE, 'billing': BILLING_REVIEW,
        'missing': Q(plan__estimated_hours__isnull=True)}
    if attention in filters:
        projects = projects.filter(filters[attention])
    summary = projects.aggregate(projects=Count('pk'), recorded_seconds=Sum('seconds'), revisions=Sum('revision_seconds'),
        over=Count('pk', filter=OVER_ESTIMATE), billing=Count('pk', filter=BILLING_REVIEW))
    summary['hours'] = hours(summary['recorded_seconds'] or 0)
    summary['revision_hours'] = hours(summary['revisions'] or 0)
    page = Paginator(projects.order_by('name', 'pk'), 30).get_page(request.GET.get('page'))
    visible_projects = list(page)
    invoices = defaultdict(list)
    for invoice in FreshBooksInvoice.objects.filter(Q(proposal__project_id__in=[p.pk for p in visible_projects]) | Q(project_id__in=[p.pk for p in visible_projects])).exclude(state='released').select_related('proposal','time_batch').order_by('created_at'):
        invoices[invoice.proposal.project_id if invoice.proposal_id else invoice.project_id].append(invoice)
    return render(request, 'workqueue/project_reports.html', {
        'page': page, 'rows': [report_row(p, invoices[p.pk]) for p in visible_projects],
        'summary': summary, 'q': q, 'scope': scope, 'attention': attention,
    })
