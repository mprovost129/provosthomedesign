from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse,Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from .client_views import intake_enabled,limited
from .pm_views import staff_view
from .portal_review_views import client_review,submitted_staff_review
from .portal_views import private_view
from .portal_export_models import PlanReviewExport
from .portal_export_worker import request_export
from .uploads import private_storage

def export_action(request,record,staff):
    if not record.submitted_at:raise Http404
    destination='workqueue:staff_plan_review' if staff else 'workqueue:portal_review'
    project_id=record.document_version.document.project_id
    if request.method=='POST':
        from .views import staff_preview
        if staff and staff_preview(request):raise PermissionDenied
        if not limited(request,'plan-export',record.author_email,limit=10,window=3600):
            messages.error(request,'Too many export requests. Please try again later.')
        else:
            try:request_export(record.pk)
            except ValidationError as exc:messages.error(request,' '.join(exc.messages))
            else:messages.success(request,'Marked plan export requested. It will appear here when ready; refresh this review shortly.')
        return redirect(destination,project_id,record.pk)
    export=PlanReviewExport.objects.filter(review=record,state='ready').first()
    if not export or not export.storage_key:return redirect(destination,project_id,record.pk)
    try:content=private_storage().open(export.storage_key,'rb')
    except (FileNotFoundError,OSError):raise Http404 from None
    response=FileResponse(content,as_attachment=True,filename=f'{record.work_item.reference}-marked-review.pdf',content_type='application/pdf')
    response['Content-Security-Policy']="default-src 'none'; sandbox"
    response['X-Content-Type-Options']='nosniff'
    return response

@intake_enabled
@private_view
@require_http_methods(['GET','POST'])
def client_export(request,project_id,review_id):
    _,record=client_review(request,project_id,review_id)
    return export_action(request,record,False)

@staff_view
@private_view
@require_http_methods(['GET','POST'])
def staff_export(request,project_id,review_id):
    return export_action(request,submitted_staff_review(project_id,review_id),True)
