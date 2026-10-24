"""Stripe sandbox attempts. These records can never become live payments."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

RESERVED_STATES = ['creating', 'open', 'processing', 'review']


class NativePaymentAttempt(models.Model):
    class State(models.TextChoices):
        CREATING = 'creating', 'Opening checkout / confirmation uncertain'
        OPEN = 'open', 'Ready for test payment'
        PROCESSING = 'processing', 'Bank payment pending'
        PAID = 'paid', 'Test payment confirmed'
        FAILED = 'failed', 'Test payment failed'
        EXPIRED = 'expired', 'Unused checkout closed'
        REVIEW = 'review', 'Needs payment review'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey('NativeInvoice', on_delete=models.PROTECT, related_name='payment_attempts')
    invoice_version = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=10, choices=[('card', 'Credit / debit card'), ('bank', 'ACH bank payment')])
    state = models.CharField(max_length=12, choices=State.choices, default=State.CREATING)
    account_id = models.CharField(max_length=100)
    session_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    payment_intent_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    checkout_url = models.TextField(max_length=3000, blank=True)
    expires_at = models.DateTimeField()
    ledger_entry = models.OneToOneField('NativeInvoiceEntry', null=True, blank=True, on_delete=models.PROTECT, related_name='stripe_test_attempt')
    issue_code = models.CharField(max_length=60, blank=True)
    adjustment_generation = models.PositiveBigIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte='.50', amount__lte='999999.99'), name='wq_test_payment_amount'),
            models.CheckConstraint(condition=Q(method__in=['card', 'bank']), name='wq_test_payment_method'),
            models.CheckConstraint(condition=Q(state__in=['creating','open','processing','paid','failed','expired','review']), name='wq_test_payment_state'),
            models.UniqueConstraint(fields=['invoice'], condition=Q(state__in=['creating','open','processing']), name='wq_test_payment_reservation'),
        ]


class NativeStripeEvent(models.Model):
    event_id = models.CharField(max_length=255, primary_key=True)
    attempt = models.ForeignKey(NativePaymentAttempt, on_delete=models.PROTECT, related_name='stripe_events')
    kind = models.CharField(max_length=80)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-received_at', '-event_id']
