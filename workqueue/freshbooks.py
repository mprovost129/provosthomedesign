"""FreshBooks OAuth, explicit draft invoices, and read-only billing reconciliation.

Tokens are encrypted with a dedicated deployment key. Never log provider bodies,
retry ambiguous financial writes, send invoices, or collect card information.
"""
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit
import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from .crm import require_staff
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink, FreshBooksInvoice
from .pm_models import Proposal
from .pm_services import current_client, event
from .services import lock_queue, EditConflict

API = 'https://api.freshbooks.com'
SCOPES = 'user:profile:read user:clients:read user:invoices:read user:invoices:write user:online_payments:read user:online_payments:write'


class BillingError(Exception):
    def __init__(self, message='FreshBooks is unavailable. Please try again later.', status=None):
        self.status = status
        super().__init__(message)


def configured():
    return bool(getattr(settings, 'FRESHBOOKS_ENABLED', False) and
        all(getattr(settings, name, '') for name in ['FRESHBOOKS_CLIENT_ID', 'FRESHBOOKS_CLIENT_SECRET',
            'FRESHBOOKS_REDIRECT_URI', 'FRESHBOOKS_TOKEN_ENCRYPTION_KEY']))


def cipher():
    if not configured():
        raise BillingError('FreshBooks connection settings have not been configured yet.')
    try:
        return Fernet(settings.FRESHBOOKS_TOKEN_ENCRYPTION_KEY.encode('ascii'))
    except (ValueError, UnicodeError):
        raise BillingError('FreshBooks token protection needs configuration.') from None


def redirect_uri():
    uri = settings.FRESHBOOKS_REDIRECT_URI
    parsed = urlsplit(uri)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise BillingError('Configure an HTTPS FreshBooks callback without query parameters.')
    return uri


def authorization_url(state):
    cipher()
    return 'https://auth.freshbooks.com/oauth/authorize/?' + urlencode(dict(
        response_type='code', client_id=settings.FRESHBOOKS_CLIENT_ID, redirect_uri=redirect_uri(),
        state=state, scope=SCOPES))


def wire(method, path, *, token=None, data=None, params=None):
    if not path.startswith('/') or path.startswith('//') or '://' in path:
        raise BillingError('Invalid FreshBooks endpoint.')
    try:
        response = requests.request(method, API+path, json=data, params=params,
            headers={'Authorization': 'Bearer '+token} if token else {}, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException:
        raise BillingError() from None
    if not 200 <= response.status_code < 300:
        message = ('FreshBooks access needs reconnecting.' if response.status_code == 401 else
            'FreshBooks denied this action. Check the connection permissions.' if response.status_code == 403 else
            'FreshBooks rejected the draft. Review its client and pricing before retrying.' if 400 <= response.status_code < 500 else
            'FreshBooks is unavailable. Please try again later.')
        raise BillingError(message, response.status_code)
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError
        return body
    except ValueError:
        raise BillingError('FreshBooks returned an unreadable response.') from None


def token_exchange(**grant):
    return wire('POST', '/auth/oauth/token', data=dict(client_id=settings.FRESHBOOKS_CLIENT_ID,
        client_secret=settings.FRESHBOOKS_CLIENT_SECRET, redirect_uri=redirect_uri(), **grant))


def store_tokens(connection, result):
    try:
        access, refresh = result['access_token'], result['refresh_token']
        lifetime = int(result['expires_in'])
        if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh or lifetime <= 0:
            raise ValueError
        connection.encrypted_tokens = cipher().encrypt(json.dumps({'access':access, 'refresh':refresh}).encode()).decode()
        connection.expires_at = timezone.now()+timedelta(seconds=lifetime)
        connection.needs_reconnect = False
        connection.save()
    except (KeyError, TypeError, ValueError):
        raise BillingError('The FreshBooks authorization response was incomplete. Reconnect the account.') from None


def access_token():
    # FreshBooks refresh tokens rotate once. A DB lock serializes web and worker refresh.
    # Commit either the new pair or the reconnect flag before raising an error.
    cipher()
    error, access = None, None
    with transaction.atomic():
        connection = FreshBooksConnection.objects.select_for_update().filter(pk=1).first()
        if not connection or not connection.encrypted_tokens or connection.needs_reconnect:
            raise BillingError('Connect FreshBooks before using billing actions.')
        try:
            tokens = json.loads(cipher().decrypt(connection.encrypted_tokens.encode()))
            if connection.expires_at and connection.expires_at > timezone.now()+timedelta(seconds=60):
                access = tokens['access']
            else:
                result = token_exchange(grant_type='refresh_token', refresh_token=tokens['refresh'])
                store_tokens(connection, result)
                access = result['access_token']
        except (InvalidToken, ValueError, KeyError, BillingError) as exc:
            connection.needs_reconnect = True
            connection.save(update_fields=['needs_reconnect', 'updated_at'])
            error = BillingError('FreshBooks authorization needs reconnecting. An interrupted refresh is not retried.')
    if error:
        raise error
    return access


def api(method, path, **kwargs):
    try:
        return wire(method, path, token=access_token(), **kwargs)
    except BillingError as exc:
        if exc.status == 401:
            FreshBooksConnection.objects.filter(pk=1).update(needs_reconnect=True)
        raise


def connect(code, actor):
    require_staff(actor, 'workqueue.change_workitem')
    cipher()
    # Persist the new token pair before fetching profile; a later error must not lose it.
    error = None
    with transaction.atomic():
        connection, _ = FreshBooksConnection.objects.get_or_create(pk=1)
        connection = FreshBooksConnection.objects.select_for_update().get(pk=1)
        connection.connected_by = actor
        connection.account_id, connection.business_name = '', ''
        try:
            store_tokens(connection, token_exchange(grant_type='authorization_code', code=code))
        except BillingError as exc:
            connection.needs_reconnect = True
            connection.save(update_fields=['needs_reconnect', 'updated_at'])
            error = exc
    if error:
        raise error
    identity = api('GET', '/auth/api/v1/users/me').get('response', {})
    businesses = []
    for membership in identity.get('business_memberships', []):
        business = membership.get('business', {})
        account = business.get('account_id', '')
        if membership.get('role') in {'owner', 'admin', 'business_partner', 'business_manager'} and re.fullmatch(r'[A-Za-z0-9_-]{1,50}', account):
            businesses.append({'account_id':account, 'name':str(business.get('name', account))[:200]})
    FreshBooksConnection.objects.filter(pk=1).update(businesses=businesses)
    if not businesses:
        raise BillingError('This FreshBooks login has no accessible billing business.')
    return businesses


def account_id():
    connection = FreshBooksConnection.objects.filter(pk=1, needs_reconnect=False).first()
    if not connection or not connection.account_id or not connection.encrypted_tokens:
        raise BillingError('Connect FreshBooks and select the business before using billing actions.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', connection.account_id):
        raise BillingError('The FreshBooks account reference needs review.')
    return connection.account_id


def choose_business(account, actor):
    require_staff(actor, 'workqueue.change_workitem')
    with transaction.atomic():
        connection = FreshBooksConnection.objects.select_for_update().get(pk=1)
        business = next((b for b in connection.businesses if b['account_id'] == account), None)
        if not business or connection.needs_reconnect:
            raise ValidationError('Choose a business from the authorized FreshBooks account.')
        if FreshBooksInvoice.objects.filter(state__in=['queued', 'creating']).exclude(account_id=account).exists():
            raise ValidationError('Finish pending invoice drafts before changing the billing business.')
        connection.account_id, connection.business_name = account, business['name']
        connection.save()


def find_clients(query):
    account = account_id()
    key = 'search[email]' if '@' in query else 'search[user_like]'
    body = api('GET', f'/accounting/account/{account}/users/clients', params={key:query[:200], 'per_page':25, 'search[vis_state]':0})
    return body.get('response', {}).get('result', {}).get('clients', [])


def remote_client(customer_id):
    account = account_id()
    body = api('GET', f'/accounting/account/{account}/users/clients/{int(customer_id)}')
    client = body.get('response', {}).get('result', {}).get('client')
    if not client or int(client.get('id', 0)) != int(customer_id) or client.get('vis_state', 0) != 0:
        raise BillingError('This FreshBooks client is unavailable. Choose an active client.')
    return client


def link_client(client, customer_id, actor):
    require_staff(actor, 'workqueue.change_workitem')
    client = current_client(client)
    account, remote = account_id(), remote_client(customer_id)
    label = (remote.get('organization') or ' '.join(filter(None, [remote.get('fname'), remote.get('lname')])) or 'FreshBooks client')[:250]
    try:
        with transaction.atomic():
            lock_queue()
            linked = FreshBooksClientLink.objects.filter(account_id=account, customer_id=customer_id).first()
            if linked and current_client(linked.client).pk != client.pk:
                raise ValidationError('That FreshBooks client is connected to another CRM client. Review or merge them first.')
            if linked and linked.client_id != client.pk:
                linked.delete()
            link, _ = FreshBooksClientLink.objects.update_or_create(client=client, account_id=account,
                defaults={'customer_id':customer_id, 'label':label, 'email':remote.get('email') or '', 'actor':actor})
            return link
    except IntegrityError:
        raise ValidationError('This billing connection changed. Reload before linking the client.') from None


@transaction.atomic
def queue_invoice(proposal_id, version, actor, confirm=False):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    proposal = Proposal.objects.select_related('current_revision', 'client').get(pk=proposal_id)
    existing = FreshBooksInvoice.objects.filter(proposal=proposal).first()
    if existing:
        return existing
    if not confirm:
        raise ValidationError('Confirm that you want to prepare a FreshBooks invoice draft.')
    if proposal.version != version:
        raise EditConflict('The proposal changed. Reload and review it before preparing an invoice.')
    revision = proposal.current_revision
    if proposal.has_draft or not revision or revision.state != 'accepted':
        raise ValidationError('Only the current accepted proposal can be invoiced.')
    snapshot = revision.snapshot
    if snapshot['pricing'] != 'fixed' or Decimal(snapshot['total']) <= 0:
        raise ValidationError('Hourly estimates and zero totals need an invoice reviewed directly in FreshBooks.')
    account = account_id()
    client = current_client(proposal.client)
    link = FreshBooksClientLink.objects.filter(client=client, account_id=account).first()
    if not link:
        raise ValidationError('Connect the billing client to an existing FreshBooks client first.')
    marker = 'PHD-'+proposal.pk.hex
    payload = {'invoice':{'customerid':link.customer_id, 'create_date':timezone.localdate().isoformat(),
        'currency_code':'USD', 'po_number':marker,
        'notes':f"{proposal.number} version {revision.sequence}\nProject: {snapshot['project_name']}\n{snapshot['project_address']}",
        'terms':snapshot.get('terms', ''),
        'lines':[{'name':line['description'][:100], 'description':line['description'], 'qty':line['quantity'],
            'unit_cost':{'amount':line['rate'], 'code':'USD'}} for line in snapshot['lines']]}}
    invoice = FreshBooksInvoice.objects.create(proposal=proposal, revision=revision, account_id=account,
        customer_id=link.customer_id, marker=marker, payload=payload, actor=actor)
    event(proposal.project, actor, 'FreshBooks invoice draft requested', number=proposal.number)
    return invoice


def money(invoice, name):
    try:
        amount = Decimal(invoice[name]['amount'])
        if not amount.is_finite() or abs(amount) > Decimal('999999999999.99'):
            raise ValueError
        return amount
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise BillingError('FreshBooks returned an incomplete invoice balance.') from None


def save_remote(local, remote):
    if int(remote.get('customerid', 0)) != local.customer_id:
        raise BillingError('The invoice client differs from the saved billing connection. Review FreshBooks.')
    local.invoice_id = int(remote.get('invoiceid') or remote['id'])
    local.invoice_number = str(remote.get('invoice_number', ''))[:100]
    local.billing_status = str(remote.get('v3_status') or remote.get('display_status') or 'Unknown')[:50]
    local.currency = str(remote.get('currency_code', 'USD'))[:3]
    local.amount, local.paid, local.outstanding = [money(remote, name) for name in ['amount', 'paid', 'outstanding']]
    local.state, local.last_sync_at, local.last_error = 'linked', timezone.now(), ''
    local.save()


def copy_payment_options(local):
    try:
        options = api('GET', f'/payments/account/{local.account_id}/payment_options', params={'entity_type':'invoice'}).get('payment_options') or {}
        if options.get('gateway_name') not in {'stripe', 'fbpay', 'paypal'}:
            raise BillingError('Review payment options directly in FreshBooks before sending this invoice.')
        allowed = ['has_credit_card', 'has_ach_transfer', 'has_bacs_debit', 'has_sepa_debit', 'has_paypal_smart_checkout', 'allow_partial_payments']
        data = {key:value for key,value in options.items() if key in allowed and isinstance(value, bool)}
        data.update(gateway_name=options['gateway_name'], entity_id=local.invoice_id, entity_type='invoice')
        api('POST', f'/payments/account/{local.account_id}/invoice/{local.invoice_id}/payment_options', data=data)
        local.payment_options_applied = True
        local.save(update_fields=['payment_options_applied'])
    except BillingError as exc:
        # The invoice already exists. Never recreate it if optional payment settings fail.
        local.last_error = str(exc)[:250]
        local.save(update_fields=['last_error'])


def process_invoice(local_id):
    # Mark intent durably before the external create. Crashes/timeouts are held for review.
    with transaction.atomic():
        local = FreshBooksInvoice.objects.select_for_update().get(pk=local_id)
        if local.state != 'queued':
            return
        local.state, local.attempted_at = 'creating', timezone.now()
        local.save(update_fields=['state', 'attempted_at'])
    posted = False
    try:
        if account_id() != local.account_id:
            raise BillingError('The connected billing business changed. Review this draft before retrying.')
        token = access_token()
        # Validate the selected billing client is still active before making a financial write.
        remote_client(local.customer_id)
        posted = True
        body = wire('POST', f'/accounting/account/{local.account_id}/invoices/invoices', token=token, data=local.payload)
        remote = body['response']['result']['invoice']
        if remote.get('po_number') != local.marker:
            raise BillingError('FreshBooks returned an unexpected invoice reference.')
        save_remote(local, remote)
    except (BillingError, KeyError, TypeError, ValueError, IntegrityError) as exc:
        status = exc.status if isinstance(exc, BillingError) else None
        local.state = 'failed' if not posted or (status is not None and 400 <= status < 500) else 'unknown'
        local.last_error = 'Draft creation needs review; check FreshBooks before trying again.'
        local.save(update_fields=['state', 'last_error'])
        return
    copy_payment_options(local)


def sync_invoice(local, reconcile=False):
    if local.account_id != account_id():
        raise BillingError('Reconnect the business associated with this invoice before refreshing it.')
    path = f'/accounting/account/{local.account_id}/invoices/invoices'
    if not local.invoice_id:
        result = api('GET', path, params={'search[po_number]':local.marker, 'per_page':100}).get('response', {}).get('result', {})
        matches = [item for item in result.get('invoices', []) if item.get('po_number') == local.marker and
            int(item.get('customerid', 0)) == local.customer_id and item.get('currency_code') == 'USD' and
            money(item, 'amount') == Decimal(local.revision.snapshot['total'])]
        if len(matches) != 1:
            raise BillingError('No single matching invoice was found. Review FreshBooks; this draft will not be recreated automatically.')
        remote = matches[0]
    else:
        remote = api('GET', path+f'/{local.invoice_id}').get('response', {}).get('result', {}).get('invoice', {})
    save_remote(local, remote)
    return local


def retry_rejected_invoice(local_id, actor, confirm=False):
    require_staff(actor, 'workqueue.change_workitem')
    if not confirm:
        raise ValidationError('Confirm retrying this rejected invoice draft after reviewing FreshBooks.')
    local = FreshBooksInvoice.objects.select_related('proposal').get(pk=local_id)
    if local.state != 'failed' or local.invoice_id:
        raise ValidationError('Only a definitively rejected draft can be retried. Uncertain creates require reconciliation.')
    if account_id() != local.account_id:
        raise ValidationError('Reconnect the original billing business before retrying.')
    path = f'/accounting/account/{local.account_id}/invoices/invoices'
    result = api('GET', path, params={'search[po_number]':local.marker, 'per_page':100}).get('response', {}).get('result', {})
    if any(item.get('po_number') == local.marker for item in result.get('invoices', [])):
        raise ValidationError('A matching invoice exists. Use Check for existing invoice instead of retrying.')
    with transaction.atomic():
        lock_queue()
        # Nullable current_revision creates an outer join. PostgreSQL cannot
        # lock its nullable side; the queue lock already serializes proposals.
        local = FreshBooksInvoice.objects.select_for_update(of=('self',)).select_related('proposal__current_revision').get(pk=local_id)
        if local.state != 'failed' or local.invoice_id:
            raise EditConflict('The invoice action changed. Reload before retrying.')
        proposal = local.proposal
        if proposal.has_draft or proposal.current_revision_id != local.revision_id or proposal.current_revision.state != 'accepted':
            raise ValidationError('The accepted proposal changed. Review billing directly in FreshBooks.')
        local.state, local.last_error = 'queued', ''
        local.save(update_fields=['state', 'last_error'])
        event(proposal.project, actor, 'Rejected FreshBooks draft retry requested', number=proposal.number)
    return local


def sync_billing(limit=10):
    if not configured():
        return 0
    connection = FreshBooksConnection.objects.filter(pk=1, needs_reconnect=False).first()
    if not connection or not connection.account_id:
        return 0
    FreshBooksInvoice.objects.filter(state='creating', attempted_at__lt=timezone.now()-timedelta(minutes=5)).update(
        state='unknown', last_error='Draft creation was interrupted. Check FreshBooks before retrying.')
    count = 0
    for pk in FreshBooksInvoice.objects.filter(state='queued', account_id=connection.account_id).values_list('pk', flat=True)[:limit]:
        process_invoice(pk)
        count += 1
    for local in FreshBooksInvoice.objects.filter(state='linked', account_id=connection.account_id).filter(
            models_q_stale()).select_related('revision').order_by('last_sync_at')[:limit]:
        try:
            sync_invoice(local)
        except BillingError as exc:
            local.last_error, local.last_sync_at = str(exc)[:250], timezone.now()
            local.save(update_fields=['last_error', 'last_sync_at'])
        count += 1
    return count


def models_q_stale():
    from django.db.models import Q
    return Q(last_sync_at__lt=timezone.now()-timedelta(minutes=15)) | Q(last_sync_at__isnull=True)
