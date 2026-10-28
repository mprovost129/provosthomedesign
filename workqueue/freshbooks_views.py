import secrets
import time
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Client
from .pm_models import Proposal
from .pm_views import staff_view, editable, private_response, error_text
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink, FreshBooksInvoice
from . import freshbooks as billing
from .pm_services import current_client
from .services import EditConflict


def owner(request):
    editable(request)
    if not request.user.is_superuser:
        raise PermissionDenied


@staff_view
@require_http_methods(['GET', 'POST'])
def connection(request):
    owner(request)
    record = FreshBooksConnection.objects.filter(pk=1).first()
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action in ('connect', 'connect_payments', 'connect_estimates'):
                state = secrets.token_urlsafe(32)
                with_payments = action == 'connect_payments' or bool(record and 'user:payments:read' in record.granted_scopes)
                with_estimates = action == 'connect_estimates' or bool(record and 'user:estimates:read' in record.granted_scopes)
                url = billing.authorization_url(state, with_payments=with_payments, with_estimates=True) if with_estimates else (
                    billing.authorization_url(state, with_payments=True) if with_payments else billing.authorization_url(state))
                request.session['freshbooks_oauth'] = {'state':state, 'time':time.time(), 'actor':request.user.pk,
                                                     'payments':with_payments,'estimates':with_estimates}
                # Keep form submissions on this site. A normal link starts OAuth without
                # weakening the site's form-action CSP for cross-origin POST redirects.
                return private_response(render(request, 'workqueue/freshbooks_connection.html',
                    {'connection':record, 'configured':True, 'authorization_url':url,
                     'callback':billing.redirect_uri()}))
            elif action == 'business':
                billing.choose_business(request.POST.get('account', ''), request.user)
                messages.success(request, 'FreshBooks billing business selected.')
            elif action == 'disconnect':
                if not request.POST.get('confirm'):
                    raise ValidationError('Confirm disconnecting this website from FreshBooks.')
                # Local disconnect immediately stops API access; invoices remain in FreshBooks.
                FreshBooksConnection.objects.filter(pk=1).update(encrypted_tokens='', needs_reconnect=True, businesses=[], granted_scopes=[], business_id=None)
                FreshBooksInvoice.objects.filter(state='queued').update(state='failed', last_error='Connection disconnected before draft creation.')
                messages.success(request, 'Website disconnected. You can also revoke this app in FreshBooks settings.')
            else:
                raise ValidationError('Choose a connection action.')
        except (billing.BillingError, ValidationError) as exc:
            messages.error(request, error_text(exc))
        return redirect('workqueue:freshbooks_connection')
    return private_response(render(request, 'workqueue/freshbooks_connection.html', {'connection':record,
        'configured':billing.configured(), 'callback':getattr(billing.settings, 'FRESHBOOKS_REDIRECT_URI', '') or
            'https://www.provosthomedesign.com'+reverse('workqueue:freshbooks_callback')}))


@staff_view
@require_http_methods(['GET'])
def callback(request):
    owner(request)
    stored = request.session.pop('freshbooks_oauth', {})
    state = request.GET.get('state', '')
    if not state or not stored or stored.get('actor') != request.user.pk or time.time()-stored.get('time', 0)>600 or not secrets.compare_digest(state, stored.get('state', '')):
        messages.error(request, 'FreshBooks authorization expired or did not match this sign-in. Start the connection again.')
    elif request.GET.get('error') or not request.GET.get('code'):
        messages.error(request, 'FreshBooks authorization was not completed.')
    else:
        try:
            if stored.get('estimates'):
                billing.connect(request.GET['code'],request.user,with_payments=bool(stored.get('payments')),with_estimates=True)
            elif stored.get('payments'):
                billing.connect(request.GET['code'], request.user, with_payments=True)
            else:
                billing.connect(request.GET['code'], request.user)
        except billing.BillingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'FreshBooks authorized. Select the billing business below.')
    return private_response(redirect('workqueue:freshbooks_connection'))


@staff_view
@require_http_methods(['GET', 'POST'])
def client_link(request, client_id):
    editable(request)
    client = current_client(get_object_or_404(Client, pk=client_id))
    query = request.GET.get('q', '').strip()[:200]
    results, linked, error, status = [], None, '', 200
    try:
        account = billing.account_id()
        linked = FreshBooksClientLink.objects.filter(client=client, account_id=account).first()
        if request.method == 'POST':
            if not request.POST.get('confirm'):
                raise ValidationError('Confirm connecting this CRM client to the selected FreshBooks client.')
            customer = forms.IntegerField(min_value=1).clean(request.POST.get('customer'))
            billing.link_client(client, customer, request.user)
            messages.success(request, 'Billing client connected. Existing CRM details and FreshBooks details are preserved.')
            return redirect('workqueue:freshbooks_client', client.pk)
        if query:
            results = billing.find_clients(query)
    except (billing.BillingError, ValidationError) as exc:
        error, status = error_text(exc), 400 if request.method == 'POST' else 200
    return render(request, 'workqueue/freshbooks_client.html', {'billing_client':client, 'linked':linked,
        'results':results, 'q':query, 'error':error}, status=status)


@staff_view
@require_http_methods(['POST'])
def invoice_action(request, proposal_id):
    editable(request)
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    try:
        action = request.POST.get('action')
        if action == 'create':
            billing.queue_invoice(proposal.pk, forms.IntegerField(min_value=1).clean(request.POST.get('version')),
                request.user, confirm=bool(request.POST.get('confirm')))
            messages.success(request, 'Invoice draft requested. Review and send it from FreshBooks once it is ready.')
        elif action == 'refresh':
            local = get_object_or_404(FreshBooksInvoice.objects.select_related('revision'), proposal=proposal)
            if local.state in {'queued', 'creating'}:
                raise ValidationError('This draft is being prepared. Refresh after the background run finishes.')
            billing.sync_invoice(local)
            messages.success(request, 'FreshBooks billing status refreshed.')
        elif action == 'retry':
            local = get_object_or_404(FreshBooksInvoice, proposal=proposal)
            billing.retry_rejected_invoice(local.pk, request.user, confirm=bool(request.POST.get('confirm')))
            messages.success(request, 'Rejected draft queued for another attempt.')
        else:
            raise ValidationError('Choose a billing action.')
    except (billing.BillingError, ValidationError, EditConflict) as exc:
        messages.error(request, error_text(exc))
    return redirect('workqueue:proposal_detail', proposal.pk)
