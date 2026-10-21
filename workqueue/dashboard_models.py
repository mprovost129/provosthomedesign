"""Account-scoped summaries from complete, read-only provider scans."""
from django.db import models
from django.db.models import Q


class DashboardBillingSummary(models.Model):
    account_id = models.CharField(max_length=50)
    kind = models.CharField(max_length=10, choices=[('invoices', 'Invoice balances'), ('payments', 'Invoice payments')])
    period = models.CharField(max_length=7)  # 'all' or YYYY-MM in the site's Eastern timezone
    totals = models.JSONField(default=dict)
    checked_at = models.DateTimeField(null=True)
    record_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=10, default='queued', choices=[('queued','Queued'), ('scanning','Checking'), ('idle','Checked'), ('failed','Needs review')])
    requested_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=300, blank=True)
    page = models.PositiveIntegerField(default=1)
    pages = models.PositiveIntegerField(null=True)
    expected_total = models.PositiveIntegerField(null=True)
    seen_ids = models.JSONField(default=list)
    working_totals = models.JSONField(default=dict)
    working_excluded = models.PositiveIntegerField(default=0)
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_id','kind','period'], name='wq_dashboard_billing_key'),
            models.CheckConstraint(condition=Q(kind__in=['invoices','payments']), name='wq_dashboard_billing_kind'),
            models.CheckConstraint(condition=Q(state__in=['queued','scanning','idle','failed']), name='wq_dashboard_billing_state')]
