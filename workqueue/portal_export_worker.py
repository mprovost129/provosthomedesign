"""Private, idempotent export jobs; heavy PDF parsing never runs in a web request."""
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from .portal_export_models import PlanReviewExport
from .portal_review_models import PlanReview
from .services import lock_queue
from .uploads import private_storage

MAX_INPUT = 100 * 1024 * 1024
MAX_OUTPUT = 110 * 1024 * 1024
FAILURE_MESSAGE = 'This plan could not be exported within the processing limits. Use an unlocked, static PDF, or download the original plan and review report separately.'

@transaction.atomic
def request_export(review_id):
    lock_queue()
    review = PlanReview.objects.get(pk=review_id, submitted_at__isnull=False)
    record, created = PlanReviewExport.objects.get_or_create(review=review)
    if record.state == 'failed':
        if record.attempts >= 3:
            raise ValidationError('This export failed three times. Use the original PDF and review report, or ask us for an optimized plan copy.')
        record.state, record.error = 'pending', ''
        record.attempt_token = uuid.uuid4()
        record.started_at = record.completed_at = None
        record.save(update_fields=['state','error','attempt_token','started_at','completed_at'])
    return record

def run_parser(source, notes, output):
    helper = Path(__file__).with_name('portal_export_pdf.py')
    # Isolated Python, no inherited API/database/email credentials, no user-controlled command text.
    paths = [str(Path(path).resolve()) for path in sys.path if path and Path(path).is_dir()]
    bootstrap = 'import sys,json,runpy;sys.path.extend(json.loads(sys.argv.pop(1)));runpy.run_path(sys.argv[1],run_name="__main__")'
    env = {name:os.environ[name] for name in ['SYSTEMROOT','WINDIR','PATH','TMP','TEMP'] if name in os.environ}
    subprocess.run([sys.executable,'-I','-c',bootstrap,json.dumps(paths),str(helper),str(source),str(notes),str(output)],
        env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)

def build_export(record):
    revision = record.review.document_version
    if revision.external_url or not revision.storage_key or revision.content_type != 'application/pdf' or revision.size_bytes > MAX_INPUT:
        raise ValidationError(FAILURE_MESSAGE)
    with TemporaryDirectory(prefix='phd-plan-review-') as directory:
        source, notes, output = [Path(directory)/name for name in ['source.pdf','review.json','marked.pdf']]
        count = 0
        with private_storage().open(revision.storage_key,'rb') as original, source.open('wb') as target:
            while block := original.read(1024*1024):
                count += len(block)
                if count > MAX_INPUT: raise ValidationError(FAILURE_MESSAGE)
                target.write(block)
        if count != revision.size_bytes: raise ValidationError(FAILURE_MESSAGE)
        review = record.review
        notes.write_text(json.dumps({'annotations':review.annotations,'geometry':review.geometry,
            'title':review.document_title,'sequence':revision.sequence,'file':revision.original_name,
            'project':revision.document.project.name,'reference':review.work_item.reference,
            'author':review.author_name,'company':review.company_name,'email':review.author_email,
            'summary':review.summary,'submitted':timezone.localtime(review.submitted_at).strftime('%b %d, %Y at %I:%M %p %Z')}),encoding='utf-8')
        run_parser(source,notes,output)
        size = output.stat().st_size
        if not 0 < size <= MAX_OUTPUT: raise ValidationError(FAILURE_MESSAGE)
        with output.open('rb') as content:
            if content.read(5) != b'%PDF-': raise ValidationError(FAILURE_MESSAGE)
            content.seek(0)
            key = f'intake/review-exports/{review.pk}/{record.attempt_token}.pdf'
            saved_key = private_storage().save(key,File(content,name='marked-review.pdf'))
        return saved_key,size

def process_exports(limit=1):
    # Crash recovery never silently leaves a client waiting. Retrying requires an explicit POST.
    PlanReviewExport.objects.filter(state='processing',started_at__lt=timezone.now()-timedelta(minutes=5)).update(
        state='failed',error='Export processing was interrupted. You can request it again.',completed_at=timezone.now())
    processed = 0
    for _ in range(limit):
        with transaction.atomic():
            record = PlanReviewExport.objects.select_for_update(skip_locked=True).filter(state='pending').order_by('created_at').first()
            if not record: break
            record.state,record.started_at = 'processing',timezone.now()
            record.attempts += 1
            record.save(update_fields=['state','started_at','attempts'])
        record = PlanReviewExport.objects.select_related('review__document_version__document__project','review__work_item').get(pk=record.pk)
        try:
            key,size = build_export(record)
        except Exception:
            # Provider/parser exceptions may contain private paths or plan text; never return or log them.
            values = {'state':'failed','error':FAILURE_MESSAGE,'completed_at':timezone.now()}
        else:
            values = {'state':'ready','storage_key':key,'size_bytes':size,'error':'','completed_at':timezone.now()}
        PlanReviewExport.objects.filter(pk=record.pk,state='processing',attempt_token=record.attempt_token).update(**values)
        processed += 1
    return processed
