"""Private reusable task lists and durable application records."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from .models import WorkProject


class ProjectChecklist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    titles = models.JSONField(default=list)
    archived = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class ChecklistApplication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name='checklist_applications')
    checklist = models.ForeignKey(ProjectChecklist, on_delete=models.PROTECT, related_name='applications')
    checklist_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
