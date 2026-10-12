"""Local billing references; FreshBooks owns invoice amounts and payment records."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .models import Client
from .pm_models import Proposal, ProposalRevision


class FreshBooksConnection(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    encrypted_tokens = models.TextField(blank=True, editable=False)
    expires_at = models.DateTimeField(null=True)
    account_id = models.CharField(max_length=50, blank=True)
    business_name = models.CharField(max_length=200, blank=True)
    businesses = models.JSONField(default=list)
    needs_reconnect = models.BooleanField(default=False)
    connected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='wq_fb_connection_singleton')]


class FreshBooksClientLink(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='billing_links')
    account_id = models.CharField(max_length=50)
    customer_id = models.PositiveIntegerField()
    label = models.CharField(max_length=250)
    email = models.EmailField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_id', 'client'], name='wq_fb_local_client_unique'),
                       models.UniqueConstraint(fields=['account_id', 'customer_id'], name='wq_fb_customer_unique')]


class FreshBooksInvoice(models.Model):
    proposal = models.OneToOneField(Proposal, null=True, blank=True, on_delete=models.PROTECT, related_name='billing_invoice')
    project = models.ForeignKey('workqueue.WorkProject', null=True, blank=True, on_delete=models.PROTECT, related_name='hourly_invoices')
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.PROTECT, related_name='hourly_invoices')
    revision = models.ForeignKey(ProposalRevision, null=True, blank=True, on_delete=models.PROTECT)
    account_id = models.CharField(max_length=50)
    customer_id = models.PositiveIntegerField()
    marker = models.CharField(max_length=100, unique=True)
    payload = models.JSONField()
    state = models.CharField(max_length=20, choices=[('queued', 'Queued'), ('creating', 'Creating'),
        ('linked', 'Linked'), ('unknown', 'Check FreshBooks'), ('failed', 'Needs review'), ('released', 'Released before creation')], default='queued')
    invoice_id = models.PositiveIntegerField(null=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    billing_status = models.CharField(max_length=50, blank=True)
    currency = models.CharField(max_length=3, default='USD')
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    paid = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    outstanding = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    payment_options_applied = models.BooleanField(default=False)
    last_error = models.CharField(max_length=250, blank=True)
    last_sync_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_id', 'invoice_id'], name='wq_fb_invoice_unique'),
            models.CheckConstraint(condition=(Q(proposal__isnull=False, revision__isnull=False, project__isnull=True, client__isnull=True) | Q(proposal__isnull=True, revision__isnull=True, project__isnull=False, client__isnull=False)), name='wq_invoice_billing_source')]
