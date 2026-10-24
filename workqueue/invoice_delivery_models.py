"""Frozen invoice test deliveries. Bearer secrets live only in the pending outbox."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class NativeInvoiceDelivery(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    invoice = models.ForeignKey('NativeInvoice',on_delete=models.PROTECT,related_name='deliveries')
    allow_test_checkout = models.BooleanField(default=False)
    invoice_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    intended_contact = models.ForeignKey('ClientContact',null=True,blank=True,on_delete=models.PROTECT)
    intended_name = models.CharField(max_length=200)
    intended_email = models.EmailField()
    owner_recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField(max_length=10000,blank=True)
    notification = models.OneToOneField('NotificationDelivery',on_delete=models.PROTECT,related_name='invoice_test_delivery')
    digest = models.CharField(max_length=64,unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True,blank=True)
    revoked_reason = models.CharField(max_length=200,blank=True)
    opened_at = models.DateTimeField(null=True,blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering=['-created_at','-id']
