"""Separate provider costs from customer invoice principal; all values are cents."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q,F
from django.utils import timezone

class NativeFinanceSettings(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    enabled=models.BooleanField(default=False)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(null=True)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(id=1),name='wq_finance_singleton')]

class NativePaymentFinance(models.Model):
    attempt=models.OneToOneField('NativePaymentAttempt',primary_key=True,on_delete=models.PROTECT,related_name='finance')
    state=models.CharField(max_length=12,default='pending',choices=[('pending','Pending'),('verified','Reconciled'),('review','Needs review')])
    generation=models.PositiveBigIntegerField(default=0)
    fingerprint=models.CharField(max_length=64,blank=True)
    checked_at=models.DateTimeField(null=True,db_index=True)
    verified_at=models.DateTimeField(null=True)
    issue=models.CharField(max_length=40,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(state__in=['pending','verified','review']),name='wq_finance_state')]

class NativeBalanceEntry(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    finance=models.ForeignKey(NativePaymentFinance,on_delete=models.PROTECT,related_name='entries')
    provider_id=models.CharField(max_length=255,unique=True)
    account_id=models.CharField(max_length=255)
    source_id=models.CharField(max_length=255)
    kind=models.CharField(max_length=20)
    provider_type=models.CharField(max_length=60)
    currency=models.CharField(max_length=3,default='usd')
    amount=models.BigIntegerField()
    fee=models.BigIntegerField()
    net=models.BigIntegerField()
    fee_details=models.JSONField(default=list)
    occurred_at=models.DateTimeField(db_index=True)
    available_at=models.DateTimeField()
    availability=models.CharField(max_length=12)
    recorded_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-occurred_at','provider_id']
        constraints=[models.CheckConstraint(condition=Q(net=F('amount')-F('fee')),name='wq_finance_exact_net'),
            models.CheckConstraint(condition=Q(currency='usd'),name='wq_finance_usd'),
            models.CheckConstraint(condition=Q(availability__in=['pending','available']),name='wq_finance_availability'),
            models.CheckConstraint(condition=Q(kind__in=['payment','refund','refund_failure','dispute']),name='wq_finance_kind')]

class NativeFinanceEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    finance=models.ForeignKey(NativePaymentFinance,null=True,on_delete=models.PROTECT,related_name='events')
    action=models.CharField(max_length=60)
    details=models.JSONField(default=dict)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-id']
