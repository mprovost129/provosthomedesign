"""Payout cash transfers and manual bank confirmations, separate from invoice revenue."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q,F
from django.utils import timezone

class NativePayoutSettings(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    enabled=models.BooleanField(default=False)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(null=True)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    discovery_at=models.DateTimeField(null=True,blank=True)
    discovery_since=models.DateTimeField(null=True,blank=True)
    discovery_issue=models.CharField(max_length=40,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(id=1),name='wq_payout_settings_one')]

class NativePayout(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    provider_id=models.CharField(max_length=255,unique=True)
    account_id=models.CharField(max_length=255)
    amount=models.BigIntegerField()
    currency=models.CharField(max_length=3)
    status=models.CharField(max_length=15)
    automatic=models.BooleanField()
    method=models.CharField(max_length=15)
    reconciliation_status=models.CharField(max_length=20)
    provider_created=models.DateTimeField(db_index=True)
    arrival_date=models.DateField()
    state=models.CharField(max_length=15,default='pending')
    issue=models.CharField(max_length=40,blank=True)
    generation=models.PositiveBigIntegerField(default=0)
    version=models.PositiveIntegerField(default=1)
    provider_snapshot=models.JSONField(default=dict)
    economics_hash=models.CharField(max_length=64,blank=True)
    payout_transaction_id=models.CharField(max_length=255,blank=True)
    debit_snapshot=models.JSONField(default=dict)
    debit_amount=models.BigIntegerField(null=True)
    debit_fee=models.BigIntegerField(null=True)
    debit_net=models.BigIntegerField(null=True)
    checked_at=models.DateTimeField(null=True,db_index=True)
    verified_at=models.DateTimeField(null=True)
    class Meta:
        ordering=['-provider_created','-id']
        constraints=[models.CheckConstraint(condition=Q(state__in=['pending','verified','review','unsupported']),name='wq_payout_state'),
          models.CheckConstraint(condition=Q(status__in=['pending','in_transit','paid','failed','canceled']),name='wq_payout_status'),
          models.CheckConstraint(condition=Q(debit_amount__isnull=True,debit_fee__isnull=True,debit_net__isnull=True)|Q(debit_amount__isnull=False,debit_fee__isnull=False,debit_net__isnull=False,debit_net=F('debit_amount')-F('debit_fee')),name='wq_payout_debit_math')]

class NativePayoutTransaction(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    payout=models.ForeignKey(NativePayout,on_delete=models.PROTECT,related_name='transactions')
    provider_id=models.CharField(max_length=255)
    source_id=models.CharField(max_length=255,blank=True)
    kind=models.CharField(max_length=60)
    amount=models.BigIntegerField()
    fee=models.BigIntegerField()
    net=models.BigIntegerField()
    currency=models.CharField(max_length=3,default='usd')
    occurred_at=models.DateTimeField()
    class Meta:
        ordering=['occurred_at','provider_id']
        constraints=[models.UniqueConstraint(fields=['payout','provider_id'],name='wq_payout_txn_once'),
          models.CheckConstraint(condition=Q(currency='usd'),name='wq_payout_txn_usd'),
          models.CheckConstraint(condition=Q(net=F('amount')-F('fee')),name='wq_payout_txn_math')]

class NativeBankConfirmation(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    payout=models.ForeignKey(NativePayout,on_delete=models.PROTECT,related_name='bank_confirmations')
    amount=models.PositiveBigIntegerField()
    date=models.DateField()
    reference=models.CharField(max_length=100)
    economics_hash=models.CharField(max_length=64)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)
    revoked_at=models.DateTimeField(null=True)
    revoked_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='+')
    revoke_reason=models.CharField(max_length=500,blank=True)
    class Meta:
        ordering=['-created_at','-id']
        constraints=[models.UniqueConstraint(fields=['payout'],condition=Q(revoked_at__isnull=True),name='wq_one_bank_confirmation'),
          models.CheckConstraint(condition=Q(amount__gt=0),name='wq_bank_confirmation_positive')]

class NativePayoutEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    payout=models.ForeignKey(NativePayout,null=True,on_delete=models.PROTECT,related_name='events')
    action=models.CharField(max_length=80)
    details=models.JSONField(default=dict)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-id']
