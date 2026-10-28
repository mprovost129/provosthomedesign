"""Private source records; these are never a second invoice/payment ledger."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

KINDS=[('clients','Clients'),('projects','Projects'),('invoices','Invoices'),('payments','Payments'),('estimates','Estimates')]


class FreshBooksMirrorSettings(models.Model):
    account_id=models.CharField(max_length=50,primary_key=True)
    business_id=models.PositiveBigIntegerField()
    business_name=models.CharField(max_length=200)
    enabled=models.BooleanField(default=False)
    include_estimates=models.BooleanField(default=False)
    version=models.PositiveIntegerField(default=1)
    changed_at=models.DateTimeField(default=timezone.now)
    changed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)


class FreshBooksMirrorJob(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    account_id=models.CharField(max_length=50)
    business_id=models.PositiveBigIntegerField()
    kind=models.CharField(max_length=12,choices=KINDS)
    state=models.CharField(max_length=12,default='queued',choices=[('queued','Queued'),('working','Checking'),('complete','Checked'),('failed','Needs review')])
    generation=models.UUIDField(default=uuid.uuid4)
    page=models.PositiveIntegerField(default=1)
    pages=models.PositiveIntegerField(null=True)
    expected_total=models.PositiveIntegerField(null=True)
    seen_ids=models.JSONField(default=list)
    requested_at=models.DateTimeField(default=timezone.now)
    complete_at=models.DateTimeField(null=True)
    next_at=models.DateTimeField(default=timezone.now)
    lease=models.UUIDField(null=True)
    lease_until=models.DateTimeField(null=True)
    error=models.CharField(max_length=300,blank=True)
    error_reference=models.CharField(max_length=80,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['account_id','kind'],name='wq_fb_mirror_job_unique'),
            models.CheckConstraint(condition=Q(kind__in=[k for k,_ in KINDS]),name='wq_fb_mirror_job_kind'),
            models.CheckConstraint(condition=Q(state__in=['queued','working','complete','failed']),name='wq_fb_mirror_job_state')]


class FreshBooksMirrorRecord(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    account_id=models.CharField(max_length=50)
    business_id=models.PositiveBigIntegerField()
    kind=models.CharField(max_length=12,choices=KINDS)
    provider_id=models.PositiveBigIntegerField()
    customer_id=models.PositiveBigIntegerField(null=True,db_index=True)
    provider_invoice_id=models.PositiveBigIntegerField(null=True,db_index=True)
    title=models.CharField(max_length=500)
    source=models.JSONField()
    source_hash=models.CharField(max_length=64)
    revision=models.PositiveIntegerField(default=1)
    version=models.PositiveIntegerField(default=1)
    client=models.ForeignKey('Client',null=True,on_delete=models.PROTECT,related_name='freshbooks_mirror_records')
    contact=models.ForeignKey('ClientContact',null=True,on_delete=models.PROTECT,related_name='+')
    project=models.ForeignKey('WorkProject',null=True,on_delete=models.PROTECT,related_name='freshbooks_mirror_records')
    applied_fields=models.JSONField(default=dict)
    issues=models.JSONField(default=list)
    missing=models.BooleanField(default=False)
    last_seen_generation=models.UUIDField()
    verified_at=models.DateTimeField(default=timezone.now)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-verified_at','kind','provider_id']
        constraints=[models.UniqueConstraint(fields=['account_id','kind','provider_id'],name='wq_fb_mirror_source_unique'),
            models.CheckConstraint(condition=Q(kind__in=[k for k,_ in KINDS]),name='wq_fb_mirror_record_kind')]
        indexes=[models.Index(fields=['account_id','kind','customer_id'],name='wq_fb_mirror_customer')]


class FreshBooksMirrorRevision(models.Model):
    record=models.ForeignKey(FreshBooksMirrorRecord,on_delete=models.PROTECT,related_name='history')
    sequence=models.PositiveIntegerField()
    source=models.JSONField()
    source_hash=models.CharField(max_length=64)
    captured_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-sequence']
        constraints=[models.UniqueConstraint(fields=['record','sequence'],name='wq_fb_mirror_revision_unique')]


class FreshBooksMirrorEvent(models.Model):
    account_id=models.CharField(max_length=50)
    record=models.ForeignKey(FreshBooksMirrorRecord,null=True,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    action=models.CharField(max_length=80)
    details=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-id']
