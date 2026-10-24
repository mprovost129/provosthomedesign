"""Owner-only sandbox receipt delivery; no client billing or provider permissions."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class NativeBillingMailSettings(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    automatic_receipts=models.BooleanField(default=False)
    enabled_since=models.DateTimeField(null=True,blank=True)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(default=timezone.now)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(id=1),name='wq_billing_mail_singleton')]


class NativeReceiptEmail(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    receipt=models.OneToOneField('NativeTestPaymentReceipt',on_delete=models.PROTECT,related_name='email_delivery')
    notification=models.OneToOneField('NotificationDelivery',on_delete=models.PROTECT,related_name='native_receipt_email')
    owner_recipient=models.EmailField()
    automatic=models.BooleanField(default=False)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)

    class Meta:
        ordering=['-created_at','-id']


class NativeBillingMailEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    action=models.CharField(max_length=100)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    delivery=models.ForeignKey(NativeReceiptEmail,null=True,blank=True,on_delete=models.PROTECT,related_name='events')
    details=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(default=timezone.now)

    class Meta:
        ordering=['-created_at','-id']
