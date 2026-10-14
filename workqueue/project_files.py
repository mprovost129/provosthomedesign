"""Staff source attachments on existing jobs; no intake, release, or mail."""
import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .crm import require_staff
from .models import Attachment, PendingUpload, Submission, WorkItem
from .pm_services import event
from .services import lock_queue
from .uploads import MAX_FILES


class ProjectFilesForm(forms.Form):
    intake_token = forms.CharField(widget=forms.HiddenInput)
    upload_ids = forms.CharField(max_length=400, widget=forms.HiddenInput)
    work_item = forms.ModelChoiceField(queryset=WorkItem.objects.none(), label='Attach to job')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['work_item'].queryset = WorkItem.objects.filter(project=project).order_by('received_at', 'pk')


@transaction.atomic
def attach_project_files(*, project, item_id, nonce, binding, ids, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    item = WorkItem.objects.select_for_update().filter(pk=item_id, project=project).first()
    if not item:
        raise ValidationError('Choose a current job belonging to this project.')
    try:
        upload_ids = [uuid.UUID(value) for value in ids.split(',') if value]
    except (ValueError, AttributeError):
        raise ValidationError('The file list is invalid. Review the uploads and try again.') from None
    if not 1 <= len(upload_ids) <= MAX_FILES or len(set(upload_ids)) != len(upload_ids):
        raise ValidationError('Select between one and ten different uploaded files.')
    manifest = {'project': str(project.pk), 'actor': actor.pk, 'binding': binding,
                'uploads': sorted(str(value) for value in upload_ids)}
    key = f'project-files:{nonce}'
    existing = Submission.objects.filter(idempotency_key=key).first()
    if existing:
        saved_event = project.planning_events.filter(action='Source files added',
            details__submission=str(existing.pk)).first()
        if (existing.work_item_id != item.pk or not saved_event
                or saved_event.details.get('manifest') != manifest):
            raise ValidationError('This upload form was already saved with different files or a different job. Open Add files again.')
        return existing, False
    uploads = list(PendingUpload.objects.select_for_update().filter(pk__in=upload_ids,
        intake_nonce=nonce, session_digest=binding, state=PendingUpload.State.READY,
        expires_at__gt=timezone.now()))
    if len(uploads) != len(upload_ids):
        raise ValidationError('A file is incomplete or expired. Retry or remove it before saving. No files were added.')
    submission = Submission.objects.create(work_item=item, idempotency_key=key,
        channel='staff_files', owner_email='', answers={'Source documents': 'Added by staff; no client notification sent.'})
    for upload in uploads:
        attachment = Attachment.objects.create(submission=submission, original_name=upload.original_name,
            storage_key=upload.storage_key, content_type=upload.content_type, size_bytes=upload.size_bytes)
        upload.state, upload.attachment = PendingUpload.State.ATTACHED, attachment
        upload.save(update_fields=['state', 'attachment'])
    event(project, actor, 'Source files added', title=f'{len(uploads)} files for {item.reference}',
          submission=str(submission.pk), manifest=manifest)
    return submission, True
