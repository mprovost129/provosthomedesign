"""Dedicated sandbox credentials; never fall back to commerce or live keys."""
from decouple import config

NATIVE_STRIPE_TEST_ENABLED = config('NATIVE_STRIPE_TEST_ENABLED', default=False, cast=bool)
NATIVE_STRIPE_TEST_SECRET_KEY = config('NATIVE_STRIPE_TEST_SECRET_KEY', default='')
NATIVE_STRIPE_TEST_WEBHOOK_SECRET = config('NATIVE_STRIPE_TEST_WEBHOOK_SECRET', default='')
NATIVE_STRIPE_TEST_REFUNDS_ENABLED = config('NATIVE_STRIPE_TEST_REFUNDS_ENABLED', default=False, cast=bool)
