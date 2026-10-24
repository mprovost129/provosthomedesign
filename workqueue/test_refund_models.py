"""Minimal provider references and append-only sandbox adjustment accounting."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

REFUND_HOLDS = ['creating', 'pending', 'requires_action', 'review']


class NativeRefundRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey('NativePaymentAttempt', on_delete=models.PROTECT, related_name='refund_requests')
    invoice_version = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    credit_work = models.BooleanField(default=False)
    note = models.TextField(max_length=2000)
    state = models.CharField(max_length=20, default='creating', choices=[
        ('creating','Refund creation uncertain'),('pending','Refund pending'),('requires_action','Refund needs action'),
        ('succeeded','Refund confirmed'),('failed','Refund failed'),('canceled','Refund canceled'),('review','Needs refund review')])
    provider_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    issue_code = models.CharField(max_length=60, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at','-id']
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name='wq_test_refund_positive'),
            models.CheckConstraint(condition=Q(state__in=['creating','pending','requires_action','succeeded','failed','canceled','review']), name='wq_test_refund_state')]


class NativePaymentAdjustment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey('NativePaymentAttempt', on_delete=models.PROTECT, related_name='adjustments')
    provider_id = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=10, choices=[('refund','Refund'),('dispute','Dispute / bank return')])
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=40)
    applied_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    request = models.OneToOneField(NativeRefundRequest, null=True, blank=True, on_delete=models.PROTECT, related_name='adjustment')
    credit_entry = models.OneToOneField('NativeInvoiceEntry', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-checked_at','-id']
        constraints = [models.CheckConstraint(condition=Q(kind__in=['refund','dispute']),name='wq_test_adjustment_kind'),
            models.CheckConstraint(condition=Q(amount__gt=0,applied_amount__gte=0,applied_amount__lte=models.F('amount')),name='wq_test_adjustment_amount')]
