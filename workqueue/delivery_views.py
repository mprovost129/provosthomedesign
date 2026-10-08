import uuid
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .access import authorized_work, new_intake_token, read_intake_token, session_digest, verified_email
from .completed_deliveries import DeliveryForm, ready_files, release_email, review_data, send_completed
from .models import CompletedFile, PendingUpload, WorkItem
from .services import EditConflict
from .uploads import private_storage
from .views import queue_enabled, staff_preview


@queue_enabled
@staff_member_required(login_url='admin:login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET', 'POST'])
def deliver_files(request, item_id):
    if staff_preview(request) or not getattr(settings, 'WORK_INTAKE_ENABLED', False):
        raise PermissionDenied
    item = get_object_or_404(WorkItem.objects.select_related('project'), pk=item_id)
    form = DeliveryForm(request.POST or None, initial={'intake_token': new_intake_token(request), 'version': item.version})
    preview, signature, status = None, '', 200
    if request.method == 'POST' and form.is_valid():
        try:
            nonce = read_intake_token(request, form.cleaned_data['intake_token'])
            if request.POST.get('action') == 'send':
                try:
                    reviewed = signing.loads(request.POST.get('preview_signature', ''), salt='workqueue.completed-review.v1', max_age=1800)
                except signing.BadSignature:
                    raise ValidationError('Preview these files again before sending. Previews expire after 30 minutes.') from None
                # Idempotent confirmation includes the original fields/binding even when
                # its uploaded objects have already been committed by a previous send.
                if (reviewed.get('nonce') != str(nonce) or reviewed.get('binding') != session_digest(request)
                    or reviewed.get('item') != str(item.pk) or reviewed.get('actor') != request.user.pk
                    or reviewed.get('version') != form.cleaned_data['version']
                    or reviewed.get('message') != form.cleaned_data['message'].strip()
                    or {x['id'] for x in reviewed['files']} != {x for x in form.cleaned_data['upload_ids'].split(',') if x}):
                    raise ValidationError('The delivery changed since preview. Preview it again before sending.')
                delivery, created = send_completed(reviewed=reviewed, actor=request.user)
                messages.success(request, 'Completed files released and email queued.' if created else 'These files were already released; no second email was created.')
                return redirect(reverse('workqueue:queue') + f'?scope=all&q={item.reference}#request-{item.pk}')
            reviewed = review_data(item, form, nonce, session_digest(request), request.user)
            if item.version != form.cleaned_data['version']:
                raise EditConflict('This request changed. Reload the current request before previewing your delivery.')
            files = ready_files(nonce=nonce, binding=session_digest(request), ids=form.cleaned_data['upload_ids'])
            preview, signature = release_email(item, reviewed, files), signing.dumps(reviewed, salt='workqueue.completed-review.v1')
        except (ValidationError, EditConflict) as exc:
            form.add_error(None, ' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            status = 409 if isinstance(exc, EditConflict) else 400
    elif request.method == 'POST':
        status = 400
    ready = []
    try:
        token = form.data.get('intake_token') if form.is_bound else form.initial['intake_token']
        nonce = read_intake_token(request, token)
        ready = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=session_digest(request), state='ready', expires_at__gt=timezone.now())
    except ValidationError:
        pass
    return render(request, 'workqueue/deliver_files.html', {'item': item, 'form': form,
        'ready_uploads': ready, 'preview': preview, 'signature': signature, 'direct_uploads': settings.INTAKE_DIRECT_UPLOADS}, status=status)


@queue_enabled
@never_cache
@require_http_methods(['GET'])
def completed_file(request, file_id):
    file = get_object_or_404(CompletedFile.objects.select_related('delivery__work_item'), pk=file_id)
    staff = request.user.is_active and request.user.is_staff and request.user.has_perm('workqueue.view_workitem')
    if not staff and (not getattr(settings, 'WORK_INTAKE_ENABLED', False) or
        not authorized_work(verified_email(request)).filter(pk=file.delivery.work_item_id).exists()):
        raise Http404
    try:
        content = private_storage().open(file.storage_key, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response = FileResponse(content, as_attachment=True, filename=file.original_name, content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response
