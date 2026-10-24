"""Staff-only trial accounting. Never feeds real receivables or payment providers."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class NativeInvoiceSequence(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_invoice = models.PositiveIntegerField(default=0)
    last_credit = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='wq_native_sequence_singleton')]


class NativeBillingPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey('InvoiceDocument', on_delete=models.PROTECT, related_name='test_billing_plans')
    revision = models.ForeignKey('InvoiceDocumentRevision', on_delete=models.PROTECT)
    agreement = models.ForeignKey('Proposal', null=True, blank=True, on_delete=models.PROTECT, related_name='test_billing_plans')
    snapshot = models.JSONField()
    total = models.DecimalField(max_digits=14, decimal_places=2)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    closed_token = models.UUIDField(null=True, blank=True, unique=True)
    closed_note = models.TextField(max_length=2000, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.CheckConstraint(condition=Q(total__gt=0), name='wq_native_plan_positive'),
            models.UniqueConstraint(fields=['document'],condition=Q(closed_at__isnull=True),name='wq_native_active_document'),
            models.UniqueConstraint(fields=['agreement'],condition=Q(closed_at__isnull=True,agreement__isnull=False),name='wq_native_active_agreement')]


class NativeInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(NativeBillingPlan, on_delete=models.PROTECT, related_name='invoices')
    number = models.CharField(max_length=30, unique=True)
    stage = models.CharField(max_length=12, choices=[('deposit','Deposit'),('progress','Progress'),('final','Final / remaining')])
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    issue_date = models.DateField()
    due_date = models.DateField()
    snapshot = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    voided_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name='wq_native_invoice_positive'),
            models.CheckConstraint(condition=Q(due_date__gte=models.F('issue_date')), name='wq_native_invoice_dates'),
            models.CheckConstraint(condition=Q(stage__in=['deposit','progress','final']), name='wq_native_invoice_stage')]


class NativeInvoiceEntry(models.Model):
    class Kind(models.TextChoices):
        PAYMENT = 'payment', 'Payment'
        CREDIT = 'credit', 'Credit'
        REVERSAL = 'reversal', 'Reversal'

    class Method(models.TextChoices):
        CHECK = 'check', 'Check'
        CASH = 'cash', 'Cash'
        BANK = 'bank', 'Bank transfer / ACH'
        CARD = 'card', 'Credit / debit card'
        ZELLE = 'zelle', 'Zelle'
        PAYPAL = 'paypal', 'PayPal'
        VENMO = 'venmo', 'Venmo'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(NativeInvoice, on_delete=models.PROTECT, related_name='entries')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    credit_number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    method = models.CharField(max_length=10, choices=Method.choices, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(max_length=2000)
    date = models.DateField()
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversal')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name='wq_native_entry_positive'),
            models.CheckConstraint(condition=Q(kind__in=['payment','credit','reversal']), name='wq_native_entry_kind'),
            models.CheckConstraint(condition=(Q(kind='reversal', reverses__isnull=False) | Q(kind__in=['payment','credit'], reverses__isnull=True)), name='wq_native_entry_reversal'),
            models.CheckConstraint(condition=(Q(kind='credit',credit_number__isnull=False) | (~Q(kind='credit') & Q(credit_number__isnull=True))), name='wq_native_credit_reference')]


class NativeInvoiceEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(NativeInvoice, on_delete=models.PROTECT, related_name='events')
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
