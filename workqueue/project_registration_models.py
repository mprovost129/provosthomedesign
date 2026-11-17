"""Audit and CRM provenance for projects registered without a queue request."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ProjectRegistration(models.Model):
    project = models.OneToOneField('WorkProject', on_delete=models.PROTECT, related_name='registration')
    contact = models.ForeignKey('ClientContact', on_delete=models.PROTECT, related_name='registered_projects')
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
