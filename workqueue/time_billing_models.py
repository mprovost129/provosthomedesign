import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .models import Client, WorkProject
from .freshbooks_models import FreshBooksInvoice
from .time_models import TimeEntry


class TimeBillingRate(models.Model):
    project=models.OneToOneField(WorkProject,on_delete=models.PROTECT,related_name='billing_rate')
    rate=models.DecimalField(max_digits=8,decimal_places=2)


class TimeBillingBatch(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    project=models.ForeignKey(WorkProject,on_delete=models.PROTECT,related_name='time_bills')
    client=models.ForeignKey(Client,on_delete=models.PROTECT)
    invoice=models.OneToOneField(FreshBooksInvoice,null=True,on_delete=models.PROTECT,related_name='time_batch')
    snapshot=models.JSONField()
    total=models.DecimalField(max_digits=14,decimal_places=2)
    created_at=models.DateTimeField(default=timezone.now)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    released_at=models.DateTimeField(null=True)


class TimeBillingLine(models.Model):
    batch=models.ForeignKey(TimeBillingBatch,on_delete=models.PROTECT,related_name='lines')
    entry=models.ForeignKey(TimeEntry,on_delete=models.PROTECT,related_name='billing_lines')
    active=models.BooleanField(default=True)
    snapshot=models.JSONField()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['entry'],condition=Q(active=True),name='wq_one_time_invoice_claim')]
