from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .pm_views import staff_view
from .freshbooks_models import FreshBooksConnection
from .invoice_overview import invoice_queryset, invoice_row, BUCKET_LABELS
from .reporting import project_report_queryset, BILLING_REVIEW


@staff_view
@require_GET
def invoice_overview(request):
    q = request.GET.get('q', '').strip()[:200]
    scope = request.GET.get('scope', 'all')
    scope = scope if scope in ('all', 'active') else 'all'
    state = request.GET.get('state', 'all')
    state = state if state in {'all', 'attention', 'stale', *BUCKET_LABELS} else 'all'
    invoices = invoice_queryset(q, scope)
    summary = invoices.aggregate(total=Count('pk'), drafts=Count('pk', filter=Q(billing_bucket='draft')),
        overdue=Count('pk', filter=Q(billing_bucket='overdue')), paid=Count('pk', filter=Q(billing_bucket='paid')),
        need_attention=Count('pk', filter=Q(attention=True)))
    balances = list(invoices.filter(receivable=True).order_by().values('currency_code').annotate(
        saved_outstanding=Sum('outstanding'), count=Count('pk'),
        overdue=Sum('outstanding', filter=Q(remote_status='overdue')),
        unverified=Count('pk', filter=Q(stale=True) | Q(needs_review=True))).order_by('currency_code'))
    projects = project_report_queryset(q).filter(BILLING_REVIEW)
    if scope == 'active':
        projects = projects.none()  # Completed projects cannot have active work.
    review_total = projects.count()
    review_projects = list(projects.order_by('name', 'pk')[:10])
    if state in BUCKET_LABELS:
        invoices = invoices.filter(billing_bucket=state)
    elif state == 'attention':
        invoices = invoices.filter(attention=True)
    elif state == 'stale':
        invoices = invoices.filter(stale=True)
    page = Paginator(invoices.order_by('-created_at', '-pk'), 30).get_page(request.GET.get('page'))
    connection = FreshBooksConnection.objects.filter(pk=1).values('account_id', 'needs_reconnect').first()
    connection_ready = bool(getattr(settings, 'FRESHBOOKS_ENABLED', False) and connection and
        connection['account_id'] and not connection['needs_reconnect'])
    return render(request, 'workqueue/invoice_overview.html', {
        'page': page, 'rows': [invoice_row(i) for i in page], 'summary': summary, 'balances': balances,
        'q': q, 'scope': scope, 'state': state, 'review_total': review_total, 'review_projects': review_projects,
        'connection_ready': connection_ready,
    })
