from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .pm_views import staff_view
from .proposal_overview import overview_queryset, overview_row, ATTENTION, LABELS


@staff_view
@require_GET
def proposal_overview(request):
    q = request.GET.get('q', '').strip()[:200]
    scope = request.GET.get('scope', 'all')
    scope = scope if scope in ('all', 'active') else 'all'
    state = request.GET.get('state', 'all')
    state = state if state in {'all', 'attention', 'expiring', 'email', *LABELS} else 'all'
    proposals = overview_queryset(q, scope)
    summary = proposals.aggregate(total=Count('pk'), attention=Count('pk', filter=ATTENTION),
        draft=Count('pk', filter=Q(overview_state='draft')), awaiting=Count('pk', filter=Q(overview_state='awaiting')),
        accepted=Count('pk', filter=Q(overview_state='accepted')))
    if state in LABELS:
        proposals = proposals.filter(overview_state=state)
    elif state == 'attention':
        proposals = proposals.filter(ATTENTION)
    elif state == 'expiring':
        proposals = proposals.filter(expiring_soon=True)
    elif state == 'email':
        proposals = proposals.filter(email_problem=True)
    page = Paginator(proposals.order_by('-updated_at', '-pk'), 30).get_page(request.GET.get('page'))
    return render(request, 'workqueue/proposal_overview.html', {
        'page':page, 'rows':[overview_row(p) for p in page], 'summary':summary,
        'q':q, 'scope':scope, 'state':state,
    })
