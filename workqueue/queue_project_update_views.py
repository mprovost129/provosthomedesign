from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .models import WorkProject
from .views import queue_enabled, staff_preview
from .queue_project_updates import project_defaults, project_label


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem','workqueue.add_workitem'),raise_exception=True)
@never_cache
@require_GET
def defaults(request,project_id):
    if staff_preview(request):raise PermissionDenied
    project=get_object_or_404(WorkProject.objects.select_related('filing_folder'),pk=project_id)
    snapshot=project_defaults(project)
    response=JsonResponse({'project_id':str(project.pk),'label':project_label(project),
        'source_request':str(snapshot['source'].pk) if snapshot['source'] else '',
        'contact_known':bool(snapshot['contact']),
        'fields':snapshot['fields'],
        'notice':'Project and client details loaded. Review the description and service for this update.' if snapshot['contact'] else
            'Project details loaded. Review the saved contact details before adding this update.'})
    response['Cache-Control']='private, no-store'
    response['X-Robots-Tag']='noindex, nofollow, noarchive'
    response['Referrer-Policy']='same-origin'
    return response
