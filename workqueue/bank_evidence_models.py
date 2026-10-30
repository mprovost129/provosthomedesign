"""Live deposit evidence, kept separate from invoice revenue and sandbox billing."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PayoutEvidenceImport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=50)
    business_id = models.PositiveBigIntegerField()
    business_name = models.CharField(max_length=200)
    month = models.DateField()
    filename = models.CharField(max_length=150)
    file_hash = models.CharField(max_length=64)
    source = models.JSONField()
    source_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['account_id', 'month', 'file_hash'], name='wq_live_payout_import_once')]


class LivePayoutEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=50)
    business_id = models.PositiveBigIntegerField()
    provider_id = models.CharField(max_length=160)
    source_import = models.ForeignKey(PayoutEvidenceImport, on_delete=models.PROTECT, related_name='current_payouts')
    source = models.JSONField()
    source_hash = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)
    missing = models.BooleanField(default=False)
    class Meta:
        ordering = ['provider_id']
        constraints = [models.UniqueConstraint(fields=['account_id', 'provider_id'], name='wq_live_payout_identity')]


class BankStatementImport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=50)
    business_id = models.PositiveBigIntegerField()
    month = models.DateField()
    bank_label = models.CharField(max_length=60)
    bank_last4 = models.CharField(max_length=4)
    currency = models.CharField(max_length=3)
    filename = models.CharField(max_length=150)
    file_hash = models.CharField(max_length=64)
    source = models.JSONField()
    source_hash = models.CharField(max_length=64)
    mapping_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    revoke_reason = models.CharField(max_length=500, blank=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['account_id', 'bank_last4', 'currency', 'file_hash', 'mapping_hash'], name='wq_bank_statement_import_once')]


class BankStatementEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    statement = models.ForeignKey(BankStatementImport, on_delete=models.PROTECT, related_name='entries')
    line_number = models.PositiveIntegerField()
    posted_date = models.DateField(db_index=True)
    amount = models.BigIntegerField()
    identity_key = models.CharField(max_length=64, db_index=True)
    economic_key = models.CharField(max_length=64, db_index=True)
    source = models.JSONField()
    source_hash = models.CharField(max_length=64)
    duplicate_in_file = models.BooleanField(default=False)
    class Meta:
        ordering = ['line_number']
        constraints = [models.UniqueConstraint(fields=['statement', 'line_number'], name='wq_bank_statement_line_once'),
            models.CheckConstraint(condition=Q(amount__gt=0), name='wq_bank_deposit_positive')]


class LiveDepositMatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.CharField(max_length=50)
    payout = models.ForeignKey(LivePayoutEvidence, on_delete=models.PROTECT, related_name='bank_matches')
    bank_entry = models.ForeignKey(BankStatementEntry, on_delete=models.PROTECT, related_name='payout_matches')
    bank_identity = models.CharField(max_length=64)
    bank_economic_key = models.CharField(max_length=64, db_index=True)
    evidence = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    revoke_reason = models.CharField(max_length=500, blank=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['payout'], condition=Q(revoked_at__isnull=True), name='wq_one_live_bank_match'),
            models.UniqueConstraint(fields=['account_id', 'bank_identity'], condition=Q(revoked_at__isnull=True), name='wq_live_bank_deposit_once'),
        ]
