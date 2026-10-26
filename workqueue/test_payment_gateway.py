"""Only Stripe test keys, direct-account events and Stripe-hosted Checkout."""
import re
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ValidationError


class GatewayUnavailable(Exception):
    pass


def configuration():
    enabled = getattr(settings, 'NATIVE_STRIPE_TEST_ENABLED', False) is True
    key = getattr(settings, 'NATIVE_STRIPE_TEST_SECRET_KEY', '')
    secret = getattr(settings, 'NATIVE_STRIPE_TEST_WEBHOOK_SECRET', '')
    test_key = isinstance(key,str) and bool(re.fullmatch(r'(?:sk|rk)_test_[A-Za-z0-9]{12,}', key))
    webhook = isinstance(secret,str) and bool(re.fullmatch(r'whsec_[A-Za-z0-9]{12,}', secret))
    return {'enabled': enabled, 'test_key_present': test_key, 'webhook_secret_present': webhook,
            'ready': enabled and test_key and webhook,
            'client_checkout_enabled': enabled and test_key and webhook and getattr(settings,'NATIVE_STRIPE_TEST_CLIENT_CHECKOUT_ENABLED',False) is True,
            'refunds_enabled': enabled and test_key and webhook and getattr(settings,'NATIVE_STRIPE_TEST_REFUNDS_ENABLED',False) is True}


def safe_checkout_url(value):
    if not isinstance(value, str) or len(value) > 3000:
        return False
    try:url = urlsplit(value)
    except ValueError:return False
    return (url.scheme == 'https' and url.netloc == 'checkout.stripe.com' and url.path.startswith('/c/pay/')
            and not any(character.isspace() for character in value))


def public_base():
    base = getattr(settings, 'INTAKE_PUBLIC_BASE_URL', '').rstrip('/')
    url = urlsplit(base)
    local = getattr(settings, 'INTAKE_LOCAL_DEVELOPMENT', False) and url.hostname in {'127.0.0.1','localhost'}
    if not (url.scheme == 'https' or (local and url.scheme == 'http')) or not url.netloc or url.username or url.password or url.query or url.fragment or url.path:
        raise ValidationError('Review the website address before testing payments.')
    return base


class TestStripeGateway:
    def __init__(self):
        if not configuration()['ready']:
            raise ValidationError('The Stripe sandbox connection is not ready. Real billing remains in FreshBooks.')
        import stripe
        self.stripe = stripe
        self.client = stripe.StripeClient(settings.NATIVE_STRIPE_TEST_SECRET_KEY,
            max_network_retries=1, http_client=stripe.RequestsClient(timeout=10))
        self._account = None

    def account_id(self):
        if self._account is None:
            try:
                self._account = self.client.v1.accounts.retrieve_current()['id']
            except self.stripe.StripeError:
                raise GatewayUnavailable('Stripe sandbox could not be reached.') from None
            if not re.fullmatch(r'acct_[A-Za-z0-9]+', self._account):
                raise GatewayUnavailable('Stripe sandbox account could not be verified.')
        return self._account

    def create(self, attempt):
        from django.urls import reverse
        metadata = {'phd_test_attempt': str(attempt.pk), 'purpose': 'phd_native_invoice_sandbox'}
        back = public_base()+(reverse('workqueue:invoice_test_payment',args=[attempt.delivery_id,attempt.pk]) if attempt.delivery_id else reverse('workqueue:test_payment_detail', args=[attempt.pk]))
        params = {'mode': 'payment', 'payment_method_types': ['card' if attempt.method == 'card' else 'us_bank_account'],
            'line_items': [{'quantity': 1, 'price_data': {'currency': 'usd', 'unit_amount': int(attempt.amount*100),
                'product_data': {'name': 'TEST ONLY - '+attempt.invoice.number}}}],
            'metadata': metadata, 'client_reference_id': str(attempt.pk), 'payment_intent_data': {'metadata': metadata},
            'success_url': back+'?returned=1', 'cancel_url': back+'?returned=1',
            'expires_at': int(attempt.expires_at.timestamp()),
            'custom_text': {'submit': {'message': 'TEST ONLY. Use Stripe test payment details. No real money is collected.'}}}
        try:
            return self.client.v1.checkout.sessions.create(params, options={'idempotency_key': 'phd-sandbox-'+str(attempt.pk)})
        except self.stripe.StripeError:
            # An uncertain POST must keep its reservation and retry the same key.
            raise GatewayUnavailable('Checkout creation is uncertain. Retry this same attempt; do not create another payment.') from None

    def retrieve(self, session_id):
        try:
            return self.client.v1.checkout.sessions.retrieve(session_id, {'expand': ['payment_intent.latest_charge']})
        except self.stripe.StripeError:
            raise GatewayUnavailable('Stripe sandbox could not confirm this checkout yet.') from None

    def expire(self, session_id):
        try:
            self.client.v1.checkout.sessions.expire(session_id)
        except self.stripe.StripeError:
            # It may already be complete. Only the following retrieval decides.
            pass
        return self.retrieve(session_id)

    def adjustments(self, intent_id, charge_id, *, disputes=False):
        def collect(resource, params):
            result=[]
            for obj in resource.list(params).auto_paging_iter():
                result.append(obj)
                if len(result)>500:
                    raise GatewayUnavailable('Too much provider history to reconcile automatically. Review this payment.')
            return result
        try:
            refunds=collect(self.client.v1.refunds,{'payment_intent':intent_id,'limit':100})
            cases=collect(self.client.v1.disputes,{'charge':charge_id,'limit':100}) if disputes else []
            return refunds,cases
        except self.stripe.StripeError:
            raise GatewayUnavailable('Stripe could not verify the full refund or dispute history. Its balance remains held for review.') from None

    def create_refund(self, request):
        if not configuration()['refunds_enabled']:
            raise ValidationError('Issuing sandbox refunds is not enabled. Real billing remains in FreshBooks.')
        try:
            return self.client.v1.refunds.create({'payment_intent':request.attempt.payment_intent_id,
                'amount':int(request.amount*100),
                'metadata':{'phd_test_refund':str(request.pk),'purpose':'phd_native_invoice_sandbox'}},
                options={'idempotency_key':'phd-test-refund-'+str(request.pk)})
        except self.stripe.StripeError:
            raise GatewayUnavailable('Refund creation is uncertain. Retry this same request; do not submit another refund.') from None

    def balance_transaction(self, transaction_id):
        """Read actual settled costs, never estimate them from a price schedule."""
        if not re.fullmatch(r'txn_[A-Za-z0-9]{1,220}',transaction_id):
            raise ValidationError('Invalid Stripe balance transaction reference.')
        try:
            return self.client.v1.balance_transactions.retrieve(transaction_id)
        except self.stripe.StripeError:
            raise GatewayUnavailable('Stripe could not confirm actual fees. Check sandbox Balance read permission.') from None
