import logging
import traceback

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import new_intake_token, read_intake_token, session_digest
from .models import PendingUpload, WorkProject
from .pm_views import editable, error_text, staff_view
from .project_files import ProjectFilesForm, attach_project_files

logger = logging.getLogger(__name__)


@staff_view
@require_http_methods(['GET', 'POST'])
def add_files(request, project_id):
    editable(request)
    if not getattr(settings, 'WORK_INTAKE_ENABLED', False):
        raise PermissionDenied
    project = get_object_or_404(WorkProject, pk=project_id)
    items = project.work_items.all().order_by('received_at', 'pk')
    form = ProjectFilesForm(request.POST or None, project=project,
        initial={'intake_token': new_intake_token(request), 'work_item': items.first()})
    status, ready = 200, []
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                nonce = read_intake_token(request, form.cleaned_data['intake_token'])
                submission, created = attach_project_files(project=project,
                    item_id=form.cleaned_data['work_item'].pk, nonce=nonce,
                    binding=session_digest(request), ids=form.cleaned_data['upload_ids'], actor=request.user)
            except ValidationError as exc:
                form.add_error(None, error_text(exc))
            except Exception as exc:
                # The atomic service rolls back the entire batch. A retry uses the
                # same nonce, so even an uncertain commit cannot add it twice.
                # Retain the failure type and call stack without logging an
                # exception message that may contain SQL values or file data.
                logger.error('Project source file save failed (%s)\n%s',
                    type(exc).__name__, ''.join(traceback.format_tb(exc.__traceback__)))
                form.add_error(None, 'The file save could not be confirmed. Retry this form; files already saved will not be added twice.')
                status = 503
            else:
                count = submission.attachments.count()
                messages.success(request, f'{count} source file(s) added to this project. No client notification was sent.' if created
                    else 'These files were already added. No duplicate files were created.')
                return redirect(reverse('workqueue:project_detail', args=[project.pk]) + '#files')
    try:
        token = form.data.get('intake_token') if form.is_bound else form.initial['intake_token']
        nonce = read_intake_token(request, token)
        ready = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=session_digest(request),
            expires_at__gt=timezone.now()).filter(Q(state=PendingUpload.State.READY) | Q(
                state=PendingUpload.State.ATTACHED,
                attachment__submission__idempotency_key=f'project-files:{nonce}',
                attachment__submission__work_item__project=project))
    except ValidationError:
        pass
    return render(request, 'workqueue/project_add_files.html', {'project': project, 'form': form,
        'ready_uploads': ready, 'direct_uploads': settings.INTAKE_DIRECT_UPLOADS}, status=status)
