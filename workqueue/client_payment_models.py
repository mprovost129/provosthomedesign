"""Receipts for verified sandbox payments; never real revenue or live receipts."""
import uuid
from django.db import models
from django.utils import timezone


class NativeTestPaymentReceipt(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    number=models.CharField(max_length=30,unique=True)
    attempt=models.OneToOneField('NativePaymentAttempt',on_delete=models.PROTECT,related_name='test_receipt')
    snapshot=models.JSONField()
    created_at=models.DateTimeField(default=timezone.now)

    class Meta:
        ordering=['-created_at','-id']
