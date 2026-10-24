"""Private invoice working papers. These are never issued invoices or receivables."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class InvoiceDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('WorkProject', on_delete=models.PROTECT, related_name='invoice_documents')
    client = models.ForeignKey('Client', on_delete=models.PROTECT, related_name='invoice_documents')
    source_revision = models.ForeignKey('ProposalRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_working_drafts')
    title = models.CharField(max_length=200)
    draft = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    @property
    def number(self):
        # A working-paper reference, not a business invoice number. FreshBooks
        # assigns the real invoice number when the owner issues it there.
        return 'DRAFT-' + self.pk.hex[:12].upper()


class InvoiceDocumentRevision(models.Model):
    document = models.ForeignKey(InvoiceDocument, on_delete=models.PROTECT, related_name='revisions')
    sequence = models.PositiveIntegerField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sequence']
        constraints = [models.UniqueConstraint(fields=['document', 'sequence'], name='wq_invoice_document_version')]
