"""Owner-only sandbox follow-up controls and durable reminder slots."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class NativeReminderSettings(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    enabled=models.BooleanField(default=False)
    enabled_since=models.DateTimeField(null=True,blank=True)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(default=timezone.now)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(id=1),name='wq_reminder_config_singleton')]


class NativeReminderPolicy(models.Model):
    invoice=models.OneToOneField('NativeInvoice',primary_key=True,on_delete=models.PROTECT,related_name='reminder_policy')
    paused=models.BooleanField(default=False)
    snooze_until=models.DateField(null=True,blank=True)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(default=timezone.now)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)


class NativeInvoiceReminder(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    invoice=models.ForeignKey('NativeInvoice',on_delete=models.PROTECT,related_name='reminders')
    stage=models.PositiveSmallIntegerField()
    original_delivery=models.ForeignKey('NativeInvoiceDelivery',on_delete=models.PROTECT)
    notification=models.OneToOneField('NotificationDelivery',on_delete=models.PROTECT,related_name='invoice_reminder')
    snapshot=models.JSONField()
    owner_recipient=models.EmailField()
    automatic=models.BooleanField(default=False)
    closed_unknown=models.BooleanField(default=False)
    review_token=models.UUIDField(unique=True,default=uuid.uuid4)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        ordering=['-created_at','-id']
        constraints=[models.UniqueConstraint(fields=['invoice','stage'],name='wq_reminder_invoice_stage'),
            models.CheckConstraint(condition=models.Q(stage__in=[1,7,14]),name='wq_reminder_stage')]


class NativeReminderEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    invoice=models.ForeignKey('NativeInvoice',null=True,blank=True,on_delete=models.PROTECT,related_name='reminder_events')
    reminder=models.ForeignKey(NativeInvoiceReminder,null=True,blank=True,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    action=models.CharField(max_length=100)
    details=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(default=timezone.now)

    class Meta:
        ordering=['-created_at','-id']
