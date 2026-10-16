"""Explicit approval, monthly numbering, and recoverable copies. Never move originals."""
import hashlib
import re
import uuid
import unicodedata
from datetime import datetime
from tempfile import SpooledTemporaryFile
from zoneinfo import ZoneInfo
from botocore.exceptions import BotoCoreError, ClientError
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from . import dropbox_provider as provider
from .dropbox_models import DropboxConnection, DropboxFolder, ProjectFolder, ProjectMonthSequence, DropboxFiling, DropboxFiledFile
from .models import Attachment, WorkItem, WorkProject, Submission, AuditEvent
from .services import lock_queue, require_queue_staff, EditConflict, edit_work
from .uploads import private_storage, MAX_BYTES

SALT = 'workqueue.dropbox.filing.v1'
FLOORS = {'2610':7}  # Explicitly confirmed by the owner; never reuse missing/deleted numbers.


def period():
    return timezone.now().astimezone(ZoneInfo('America/New_York')).strftime('%y%m')


def folder_number(name):
    match = re.match(r'^(\d{2}(?:0[1-9]|1[0-2])\d{2})\s*-', name)
    return match[1] if match else ''


def component(value):
    value = unicodedata.normalize('NFC', str(value)).strip()
    if (not value or len(value)>180 or value[-1:] in {'.',' '} or any(ord(c)<32 or c in '<>:"/\\|?*' for c in value)
            or value in {'.','..'} or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',value,re.I)):
        raise ValidationError('Use a folder name without Windows-reserved names or characters (maximum 180 characters).')
    return value


def safe_file_name(attachment):
    name = unicodedata.normalize('NFC', attachment.original_name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'document'
    stem, dot, extension = name.rpartition('.')
    if not dot:
        stem, extension = name, ''
    return attachment.pk.hex[:12]+' - '+stem[:110]+('.'+extension[:12] if extension else '')


def validate_child(item, root_path='/projects'):
    path = item.get('path_lower', '').rstrip('/')
    if item.get('.tag') != 'folder' or path.rpartition('/')[0] != root_path or not item.get('id','').startswith('id:'):
        raise provider.DropboxError('The selected folder is no longer directly inside Projects. Refresh folders and review again.')
    return item


def refresh_index():
    entries = [validate_child(entry) for entry in provider.children()]
    # A complete remote listing succeeds before the current index is replaced.
    with transaction.atomic():
        connection = DropboxConnection.objects.select_for_update().get(pk=1)
        DropboxFolder.objects.update(available=False)
        for entry in entries:
            DropboxFolder.objects.update_or_create(pk=entry['id'], defaults={
                'name':entry['name'], 'path':entry['path_display'], 'number':folder_number(entry['name']),
                'available':True, 'indexed_at':timezone.now()})
        connection.indexed_at = timezone.now()
        connection.save(update_fields=['indexed_at','updated_at'])
    return entries


def next_number(prefix=None):
    prefix = prefix or period()
    used = [int(number[4:]) for number in DropboxFolder.objects.filter(available=True,number__startswith=prefix).values_list('number',flat=True)]
    used += [int(number[4:]) for number in ProjectFolder.objects.filter(number__startswith=prefix).values_list('number',flat=True)]
    saved = ProjectMonthSequence.objects.filter(pk=prefix).first()
    counter = max([FLOORS.get(prefix,0), saved.last_number if saved else 0, *used])+1
    if counter>99:
        raise ValidationError('All 99 project numbers for this month are reserved. Review numbering before creating another project.')
    return prefix+f'{counter:02d}'


def pending_submissions(item):
    return list(item.submissions.filter(dropbox_filing__isnull=True).prefetch_related('attachments').order_by('received_at','pk'))


def manifest(submissions):
    return [{'id':str(submission.pk), 'files':[
        {'id':str(file.pk),'key':file.storage_key,'size':file.size_bytes,'name':file.original_name}
        for file in sorted(submission.attachments.all(),key=lambda file:str(file.pk))]}
        for submission in submissions]


def preview(*, item, mode, folder_id='', title='', address='', actor):
    require_queue_staff(actor,'workqueue.change_workitem')
    if item.deleted_at:
        raise ValidationError('Restore this request before filing documents.')
    entries = refresh_index()
    submissions = pending_submissions(item)
    if not submissions:
        raise ValidationError('All submissions on this request already have filing approvals.')
    snapshot = manifest(submissions)
    if any(not file['key'] for row in snapshot for file in row['files']):
        raise ValidationError('This request includes archived Google Drive files. Import those originals before approving Dropbox filing.')
    binding = ProjectFolder.objects.filter(project_id=item.project_id).first() if item.project_id else None
    number = ''
    if binding:
        if not binding.folder_id:
            raise ValidationError('This project already has a reserved folder. Finish or review its existing filing first.')
        mode, folder_id = 'existing', binding.folder_id
    if mode == 'new':
        number = next_number()
        label = component(f'{number} - {component(title)} - {component(address)}')
        target = ''
    elif mode == 'existing':
        selected = next((entry for entry in entries if entry['id']==folder_id),None)
        if not selected:
            raise ValidationError('Choose a current folder inside Projects.')
        label, target = selected['name'], selected['id']
        other = ProjectFolder.objects.filter(folder_id=target).first()
        if other and item.project_id and other.project_id != item.project_id:
            raise ValidationError('This folder is connected to another website project. Use Edit in the queue to connect this request to that project, then review filing again.')
        number = folder_number(label)
    else:
        raise ValidationError('Choose a new or existing project folder.')
    connection = DropboxConnection.objects.get(pk=1)
    payload = {'item':str(item.pk),'version':item.version,'project':str(item.project_id or ''), 'actor':actor.pk,
               'mode':mode,'folder':target,'label':label,'number':number,'period':period(),
               'account':connection.account_id,'root':connection.root_id,'submissions':snapshot}
    return payload, signing.dumps(payload,salt=SALT,compress=True)


@transaction.atomic
def approve(*, item_id, signed, actor):
    require_queue_staff(actor,'workqueue.change_workitem')
    try:
        data = signing.loads(signed,salt=SALT,max_age=900)
    except signing.BadSignature:
        raise ValidationError('The filing preview expired. Review it again before approving.') from None
    connection = DropboxConnection.objects.select_for_update().get(pk=1)
    if not connection.enabled or data['account']!=connection.account_id or data['root']!=connection.root_id:
        raise ValidationError('The Dropbox connection changed. Review filing again.')
    # Do remote I/O before locking the main queue so clients can continue submitting.
    refresh_index()
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    if data['actor']!=actor.pk or data['item']!=str(item.pk):
        raise ValidationError('This preview belongs to a different request or staff member.')
    approved_ids = [row['id'] for row in data['submissions']]
    saved = list(DropboxFiling.objects.filter(submission_id__in=approved_ids))
    if len(saved)==len(approved_ids) and all(str(job.submission.work_item_id)==data['item'] for job in saved):
        return saved
    if item.deleted_at or item.version!=data['version'] or str(item.project_id or '')!=data['project']:
        raise EditConflict('This request changed. Review the current project and files before approving.')
    submissions = pending_submissions(item)
    if manifest(submissions)!=data['submissions']:
        raise EditConflict('The submitted file list changed. Review filing again.')
    # Fresh remote metadata prevents approving a removed/moved folder or consuming a manually used number.
    binding = ProjectFolder.objects.filter(project_id=item.project_id).first() if item.project_id else None
    folder = None
    if data['mode']=='new':
        if binding or data['period']!=period() or data['number']!=next_number():
            raise EditConflict('The next project number changed. Review the updated folder preview.')
        component(data['label'])
        sequence, _ = ProjectMonthSequence.objects.get_or_create(pk=period())
        sequence.last_number = int(data['number'][4:])
        sequence.save()
    else:
        folder = DropboxFolder.objects.filter(pk=data['folder'], available=True).first()
        if not folder or folder.name!=data['label']:
            raise EditConflict('The destination folder changed. Refresh folders and preview again.')
        other = ProjectFolder.objects.filter(folder=folder).first()
        if other and item.project_id and other.project_id!=item.project_id:
            raise ValidationError('Connect the request to the existing website project using Edit before filing.')
        if not item.project_id and other:
            item = edit_work(item_id=item.pk,version=item.version,data={'project':other.project},actor=actor)
            binding = other
        if binding and binding.folder_id!=folder.pk:
            raise ValidationError('This project is already connected to a different folder.')
    if not item.project_id:
        project = WorkProject.objects.create(name=item.project_name or item.project_context or data['label'],
            street=item.project_street,city=item.project_city,state=item.project_state,zip_code=item.project_zip)
        item = edit_work(item_id=item.pk,version=item.version,data={'project':project},actor=actor)
    if not binding:
        if data['number'] and ProjectFolder.objects.filter(number=data['number']).exists():
            raise ValidationError('That project number is already reserved. Review the connected projects.')
        binding = ProjectFolder.objects.create(project=item.project,number=data['number'] or None,
            folder=folder,intended_name=data['label'])
    jobs = []
    for submission in submissions:
        batch = f'{submission.received_at.astimezone(ZoneInfo("America/New_York")):%Y-%m-%d} {item.reference} {submission.pk.hex[:8]}'
        job = DropboxFiling.objects.create(submission=submission,destination=binding,batch_name=batch,approved_by=actor)
        for file in submission.attachments.all():
            if file.size_bytes>MAX_BYTES or file.size_bytes<=0:
                raise ValidationError('An attachment has an unsupported size. Review it before filing.')
            DropboxFiledFile.objects.create(filing=job,attachment=file,name=safe_file_name(file),
                storage_key=file.storage_key,size_bytes=file.size_bytes)
        jobs.append(job)
    AuditEvent.objects.create(work_item=item,actor=actor,action='dropbox_filing_approved',
        changes={'folder':data['label'],'number':data['number'],'submissions':approved_ids})
    return jobs


def ensure_project_folder(binding):
    connection, _ = provider.root()
    if binding.folder_id:
        current = validate_child(provider.metadata(binding.folder_id))
        if folder_number(current['name']) != (binding.number or ''):
            raise provider.DropboxError('The project folder number changed. Review the destination before transferring.')
        return current
    # A number already present anywhere under Projects is never silently adopted or reused.
    entries = provider.children()
    if any(folder_number(entry['name'])==binding.number for entry in entries):
        raise provider.DropboxError('This number already exists in Dropbox. Review and explicitly attach the existing folder; nothing was overwritten.')
    result = validate_child(provider.create_folder(connection.root_id,binding.intended_name))
    folder, _ = DropboxFolder.objects.update_or_create(pk=result['id'],defaults={
        'name':result['name'],'path':result['path_display'],'number':binding.number,'available':True})
    binding.folder = folder
    binding.save(update_fields=['folder'])
    return result


def content_digest(file):
    digest = hashlib.sha256()
    while block := file.read(4*1024*1024):
        digest.update(hashlib.sha256(block).digest())
    file.seek(0)
    return digest.hexdigest()


def ensure_subfolder(parent, name):
    existing = provider.metadata(parent['id']+'/'+name, missing=True)
    child = existing or provider.create_folder(parent['id'],name)
    if child.get('.tag')!='folder' or child.get('path_lower','').rpartition('/')[0]!=parent['path_lower']:
        raise provider.DropboxError('A filing subfolder has moved or conflicts with a file. Review it before retrying.')
    return child


def copy_one(record, parent):
    # The sealed source is retained in S3. Deterministic names make ambiguous upload retries reconcilable.
    with SpooledTemporaryFile(max_size=2*1024*1024) as file:
        size = 0
        with private_storage().open(record.storage_key,'rb') as source:
            while block := source.read(1024*1024):
                size += len(block)
                if size>record.size_bytes or size>MAX_BYTES:
                    raise provider.DropboxError('The source file size changed. No replacement was uploaded.')
                file.write(block)
        if size!=record.size_bytes:
            raise provider.DropboxError('The source file is incomplete. No replacement was uploaded.')
        file.seek(0)
        digest = content_digest(file)
        existing = provider.metadata(parent['id']+'/'+record.name,missing=True)
        result = existing or provider.upload(parent['id'],record.name,file,digest)
        if (result.get('.tag','file')!='file' or result.get('size')!=size or result.get('content_hash')!=digest
                or result.get('path_lower','').rpartition('/')[0]!=parent['path_lower']):
            raise provider.DropboxError('Dropbox verification failed or a different file exists at that name. Original files were retained.')
        record.dropbox_id, record.content_hash, record.filed_at = result['id'],digest,timezone.now()
        record.save(update_fields=['dropbox_id','content_hash','filed_at'])


@transaction.atomic
def process_one():
    # One connection lock serializes approvals and workers; skip rather than overlap another long transfer.
    connection = DropboxConnection.objects.select_for_update(skip_locked=True).filter(pk=1,enabled=True).first()
    if not connection:
        return False
    job = DropboxFiling.objects.select_for_update().filter(state__in=['queued','copying']).order_by('approved_at','pk').first()
    if not job:
        return False
    try:
        if job.submission.work_item.deleted_at:
            raise provider.DropboxError('The queue request was deleted. Restore it and review filing before retrying.')
        if job.submission.work_item.project_id != job.destination.project_id:
            raise provider.DropboxError('The request is now connected to a different project. Review the filing destination.')
        destination = ensure_project_folder(job.destination)
        uploads = ensure_subfolder(destination,'Client Uploads')
        batch = ensure_subfolder(uploads,job.batch_name)
        if job.batch_id and job.batch_id!=batch['id']:
            raise provider.DropboxError('The approved submission folder was replaced. Review before retrying.')
        job.batch_id = batch['id']
        record = job.files.filter(filed_at__isnull=True).order_by('pk').first()
        if record:
            copy_one(record,batch)
        job.state = 'copying' if job.files.filter(filed_at__isnull=True).exists() else 'filed'
        job.last_error = ''
        if job.state=='filed':
            job.finished_at = timezone.now()
            AuditEvent.objects.create(work_item=job.submission.work_item,action='dropbox_filing_complete',
                changes={'filing':str(job.pk),'folder':job.destination.intended_name})
    except provider.DropboxError as exc:
        job.state,job.last_error = 'attention',str(exc)
    except (OSError, ValueError, BotoCoreError, ClientError):
        job.state,job.last_error = 'attention','The source file could not be copied. Originals remain available. Review before retrying.'
    job.save(update_fields=['state','batch_id','last_error','finished_at'])
    return True


@transaction.atomic
def retry(job_id, actor):
    require_queue_staff(actor,'workqueue.change_workitem')
    DropboxConnection.objects.select_for_update().get(pk=1)
    job = DropboxFiling.objects.select_for_update().get(pk=job_id)
    if job.state!='attention':
        raise ValidationError('Only a filing that needs attention can be retried.')
    job.state,job.last_error='queued',''
    job.save(update_fields=['state','last_error'])
    AuditEvent.objects.create(work_item=job.submission.work_item,actor=actor,action='dropbox_filing_retried',changes={'filing':str(job.pk)})


@transaction.atomic
def reconcile_folder(job_id, folder_id, actor):
    """Explicit recovery for a successful folder creation whose response/DB commit was lost."""
    require_queue_staff(actor,'workqueue.change_workitem')
    DropboxConnection.objects.select_for_update().get(pk=1)
    lock_queue()
    job = DropboxFiling.objects.select_for_update().filter(pk=job_id,state='attention').first()
    if not job:
        raise ValidationError('This filing was already changed. Reload its current status.')
    binding = job.destination
    if binding.folder_id:
        raise ValidationError('This destination is already connected. Review its existing folder.')
    current = validate_child(provider.metadata(folder_id))
    if current['name']!=binding.intended_name or folder_number(current['name'])!=binding.number:
        raise ValidationError('Choose only the exact approved folder name and number.')
    folder,_=DropboxFolder.objects.update_or_create(pk=current['id'],defaults={
        'name':current['name'],'path':current['path_display'],'number':binding.number,'available':True})
    if ProjectFolder.objects.filter(folder=folder).exists():
        raise ValidationError('That folder is already connected to another project.')
    binding.folder=folder
    binding.save(update_fields=['folder'])
    job.state,job.last_error='queued',''
    job.save(update_fields=['state','last_error'])
    AuditEvent.objects.create(work_item=job.submission.work_item,actor=actor,action='dropbox_folder_reconciled',changes={'filing':str(job.pk),'folder':current['name']})
