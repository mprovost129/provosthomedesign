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
            'ready': enabled and test_key and webhook}


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
        back = public_base()+reverse('workqueue:test_payment_detail', args=[attempt.pk])
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
