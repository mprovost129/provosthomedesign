"""Explicit staff releases; original submissions and final drawings stay distinct."""
import uuid
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.template.defaultfilters import filesizeformat
from .access import authorized_work, mint_email_token, normalize_email
from .models import AuditEvent, CompletedDelivery, CompletedFile, NotificationDelivery, PendingUpload, WorkItem
from .notifications import access_url, site_url
from .services import EditConflict, lock_queue


class DeliveryForm(forms.Form):
    intake_token = forms.CharField(widget=forms.HiddenInput)
    upload_ids = forms.CharField(max_length=400, widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    message = forms.CharField(label="Message to your client", max_length=5000, required=False,
        widget=forms.Textarea(attrs={"rows": 4, "class": "input"}))


def ready_files(*, nonce, binding, ids):
    try:
        ids = [uuid.UUID(value) for value in ids.split(',') if value]
    except ValueError:
        raise ValidationError("The file list is invalid.") from None
    if not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
        raise ValidationError("Upload between one and ten different completed files.")
    files = list(PendingUpload.objects.filter(pk__in=ids, intake_nonce=nonce, session_digest=binding,
        state="ready", expires_at__gt=timezone.now()).order_by('created_at', 'pk'))
    if len(files) != len(ids):
        raise ValidationError("A completed file is incomplete or expired. Upload it again.")
    return files


def review_data(item, form, nonce, binding, actor):
    files = ready_files(nonce=nonce, binding=binding, ids=form.cleaned_data['upload_ids'])
    return {'item': str(item.pk), 'version': form.cleaned_data['version'], 'recipient': normalize_email(item.contact_email),
            'actor': actor.pk, 'nonce': str(nonce), 'binding': binding, 'message': form.cleaned_data['message'].strip(),
            'files': [{'id': str(file.pk), 'name': file.original_name, 'size': file.size_bytes} for file in files]}


def release_email(item, reviewed, files, *, signin_url=None):
    file_lines = '\n'.join(f"{file.original_name} ({filesizeformat(file.size_bytes)})\n" +
        (site_url(reverse('workqueue:completed_file', args=[file.pk])) if isinstance(file, CompletedFile) else 'Secure download link included when sent.')
        for file in files)
    return {'recipient': reviewed['recipient'], 'subject': f"Your completed files: {item.reference}",
        'body': f"Hello {item.contact_full_name},\n\nYour completed files for request {item.reference} are available.\n\n" +
        (reviewed['message'] + '\n\n' if reviewed['message'] else '') +
        f"Sign in with this email address to open your files:\n{signin_url or site_url(reverse('workqueue:tracking'))}\n\n" +
        f"COMPLETED FILES\n{file_lines}\n\nThese are delivered drawings/documents, separate from your original submissions. " +
        "Files are linked rather than attached. If your sign-in link expires, request a fresh one at:\n" +
        site_url(reverse('workqueue:tracking')) + '\n\nProvost Home Design\n'}


@transaction.atomic
def send_completed(*, reviewed, actor):
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=reviewed['item'])
    existing = CompletedDelivery.objects.filter(pk=reviewed['nonce']).first()
    if existing:
        if existing.work_item_id != item.pk or existing.actor_id != actor.pk or existing.message != reviewed['message'] or existing.recipient != reviewed['recipient']:
            raise ValidationError("This send confirmation was already used. Prepare a new delivery.")
        return existing, False
    if reviewed['actor'] != actor.pk or item.version != reviewed['version'] or normalize_email(item.contact_email) != reviewed['recipient']:
        raise EditConflict("This request changed. Preview the current recipient and files again before sending.")
    if not authorized_work(reviewed['recipient']).filter(pk=item.pk).exists():
        raise ValidationError("This recipient does not have client access to this request. Confirm project access before delivering files.")
    uploads = ready_files(nonce=reviewed['nonce'], binding=reviewed['binding'], ids=','.join(file['id'] for file in reviewed['files']))
    if [{'id': str(x.pk), 'name': x.original_name, 'size': x.size_bytes} for x in uploads] != reviewed['files']:
        raise ValidationError("The files changed after preview. Preview them again.")
    notice = NotificationDelivery.objects.create(recipient_kind='client', recipient=reviewed['recipient'], provider_reference=f"queue-{uuid.uuid4().hex}")
    delivery = CompletedDelivery.objects.create(id=reviewed['nonce'], work_item=item, actor=actor,
        recipient=reviewed['recipient'], message=reviewed['message'], notification=notice)
    files = []
    for upload in uploads:
        files.append(CompletedFile.objects.create(delivery=delivery, upload=upload,
            original_name=upload.original_name, storage_key=upload.storage_key, size_bytes=upload.size_bytes))
        upload.state = 'attached'
        upload.save(update_fields=['state'])
    _, raw = mint_email_token(delivery.recipient, item.reference)
    email = release_email(item, reviewed, files, signin_url=access_url(raw))
    notice.subject, notice.body = email['subject'], email['body']
    notice.save(update_fields=['subject', 'body'])
    AuditEvent.objects.create(work_item=item, actor=actor, action='completed_files_released',
        changes={'delivery_id': str(delivery.pk), 'file_count': len(files), 'notification_id': notice.pk})
    item.version += 1
    item.save(update_fields=['version', 'updated_at'])
    return delivery, True
