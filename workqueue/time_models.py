"""Private time records. Tracking never creates a bill or changes queue order."""
import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .models import WorkProject, WorkItem


class TimeEntry(models.Model):
    class Category(models.TextChoices):
        DESIGN = 'design', 'Design'
        REVISION = 'revision', 'Revisions'
        MEETING = 'meeting', 'Meetings'
        ADMIN = 'admin', 'Administration'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name='time_entries')
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, null=True, blank=True, related_name='time_entries')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='project_time_entries')
    work_date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.DESIGN)
    description = models.TextField(blank=True, max_length=2000)
    billable = models.BooleanField(default=True)
    source = models.CharField(max_length=10, choices=[('manual', 'Manual'), ('timer', 'Timer')], default='manual')
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-work_date', '-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['actor'], condition=Q(source='timer', ended_at__isnull=True, deleted_at__isnull=True), name='wq_one_active_timer'),
            models.CheckConstraint(condition=Q(category__in=['design', 'revision', 'meeting', 'admin', 'other']), name='wq_time_category'),
            models.CheckConstraint(condition=(
                Q(source='manual', started_at__isnull=True, ended_at__isnull=True, duration_seconds__isnull=False, duration_seconds__gt=0) |
                Q(source='timer', started_at__isnull=False, ended_at__isnull=True, duration_seconds__isnull=True) |
                Q(source='timer', started_at__isnull=False, ended_at__isnull=False, duration_seconds__isnull=False, duration_seconds__gt=0, ended_at__gte=models.F('started_at'))
            ), name='wq_time_valid_record'),
        ]
        indexes = [models.Index(fields=['project', 'deleted_at', 'work_date'], name='wq_time_project_date')]

    @property
    def running(self):
        return self.source == 'timer' and self.ended_at is None and self.deleted_at is None

    @property
    def hours(self):
        return (Decimal(self.duration_seconds or 0)/3600).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)

    @property
    def duration_label(self):
        hours, remainder = divmod(self.duration_seconds or 0, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f'{hours:d}:{minutes:02d}:{seconds:02d}'
