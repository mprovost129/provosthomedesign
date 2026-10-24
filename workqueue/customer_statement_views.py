from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from .customer_statements import statement_for
from .models import Client
from .pm_views import staff_view


def private(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'same-origin'
    return response


@staff_view
@require_GET
def overview(request):
    q = request.GET.get('q', '').strip()[:150]
    clients = Client.objects.filter(merged_into__isnull=True)
    if q:
        clients = clients.filter(name__icontains=q)
    return private(render(request, 'workqueue/customer_statements.html', {
        'q': q, 'page': Paginator(clients, 25).get_page(request.GET.get('page'))}))


@staff_view
@require_GET
def detail(request, client_id):
    statement = statement_for(get_object_or_404(Client, pk=client_id), actor=request.user)
    if statement['client_id'] != client_id:
        return redirect('workqueue:customer_statement', statement['client_id'])
    return private(render(request, 'workqueue/customer_statement.html', {
        'statement': statement, 'activity_page': Paginator(statement['activity'], 50).get_page(request.GET.get('page'))}))


@staff_view
@require_GET
def pdf(request, client_id):
    statement = statement_for(get_object_or_404(Client, pk=client_id), actor=request.user)
    from .customer_statement_pdf import statement_pdf
    response = HttpResponse(statement_pdf(statement), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="test-customer-statement.pdf"'
    return private(response)
