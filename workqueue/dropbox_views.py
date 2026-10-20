import secrets
import time
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from . import dropbox_provider as provider
from . import dropbox_filing as filing
from . import freshbooks_projects
from .freshbooks_project_models import FreshBooksProject
from .freshbooks import BillingError
from .dropbox_models import DropboxConnection, DropboxFolder, DropboxFiling, ProjectFolder
from .models import WorkItem
from .pm_views import staff_view, editable, error_text
from .services import EditConflict


class FilingForm(forms.Form):
    mode = forms.ChoiceField(label='Where should these documents go?',choices=[('new','Create a new numbered project folder'),('existing','Use an existing project folder')])
    folder = forms.ModelChoiceField(label='Existing project folder',queryset=DropboxFolder.objects.none(),required=False)
    title = forms.CharField(label='Project name',max_length=100,required=False)
    address = forms.CharField(label='Project address',max_length=130,required=False)
    freshbooks_action = forms.ChoiceField(label='FreshBooks project',required=False,choices=[('skip','Dropbox only for now'),('create','Create or reuse the connected FreshBooks project'),('link','Connect an existing FreshBooks project')])
    freshbooks_pricing = forms.ChoiceField(label='Billing type',required=False,choices=[('fixed_price','Fixed price'),('hourly_rate','Hourly')],initial='fixed_price')
    freshbooks_amount = forms.DecimalField(label='Fixed price or hourly rate',required=False,min_value=0.01,max_value=999999999.99,decimal_places=2,widget=forms.NumberInput(attrs={'step':'0.01','placeholder':'Enter actual price or hourly rate'}))
    freshbooks_project_id = forms.IntegerField(label='Existing FreshBooks project ID',required=False,min_value=1)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['folder'].queryset=DropboxFolder.objects.filter(available=True)


@staff_view
@require_http_methods(['GET','POST'])
def connection(request):
    editable(request)
    if not request.user.is_superuser:
        raise PermissionDenied
    authorization_url = ''
    record = DropboxConnection.objects.filter(pk=1).first()
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='connect':
                state,verifier=secrets.token_urlsafe(32),secrets.token_urlsafe(48)
                authorization_url=provider.authorize(state,verifier)
                request.session['dropbox_oauth']={'state':state,'verifier':verifier,'time':time.time(),'actor':request.user.pk}
            elif action in {'freshbooks_check','freshbooks_retry','freshbooks_link'}:
                if request.POST.get('confirm')!='yes':
                    raise ValidationError('Confirm the FreshBooks recovery action after reviewing its current status.')
                if not billing_job:
                    raise ValidationError('This project has no approved FreshBooks request.')
                freshbooks_projects.recover(billing_job.pk,request.user,action=action.split('_',1)[1],
                    remote_id=forms.IntegerField(required=False,min_value=1).clean(request.POST.get('remote_id')))
                messages.success(request,'FreshBooks project status updated. No invoice or client email was sent.')
                return redirect('workqueue:dropbox_review',item.pk)
            elif action=='refresh':
                filing.refresh_index()
                messages.success(request,'Projects folder list refreshed. No folders or documents were changed.')
                return redirect('workqueue:dropbox_connection')
            elif action=='pause':
                DropboxConnection.objects.filter(pk=1).update(enabled=False)
                messages.success(request,'Dropbox filing paused. Uploaded originals and existing folders are unchanged.')
                return redirect('workqueue:dropbox_connection')
            else:
                raise ValidationError('Choose a connection action.')
        except (provider.DropboxError,ValidationError) as exc:
            messages.error(request,error_text(exc))
    return render(request,'workqueue/dropbox_connection.html',{'connection':record,
        'configured':provider.configured(),'authorization_url':authorization_url,'callback':provider.CALLBACK,
        'folder_count':DropboxFolder.objects.filter(available=True).count()})


@staff_view
@require_http_methods(['GET'])
def callback(request):
    editable(request)
    if not request.user.is_superuser:
        raise PermissionDenied
    stored=request.session.pop('dropbox_oauth',{})
    state=request.GET.get('state','')
    if (not state or not stored or stored.get('actor')!=request.user.pk or
            time.time()-stored.get('time',0)>600 or not secrets.compare_digest(state,stored.get('state',''))):
        messages.error(request,'Dropbox authorization expired or did not match this sign-in. Start again.')
    elif request.GET.get('error') or not request.GET.get('code'):
        messages.error(request,'Dropbox authorization was not completed.')
    else:
        try:
            provider.connect(request.GET['code'],stored['verifier'])
            filing.refresh_index()
        except provider.DropboxError as exc:
            messages.error(request,str(exc))
        else:
            messages.success(request,'Dropbox connected. Each request still needs your filing approval.')
    response=redirect('workqueue:dropbox_connection')
    response['Cache-Control']='private, no-store'
    response['Referrer-Policy']='no-referrer'
    return response


@staff_view
@require_http_methods(['GET','POST'])
def review(request,item_id):
    editable(request)
    item=get_object_or_404(WorkItem.objects.select_related('project'),pk=item_id)
    binding=ProjectFolder.objects.filter(project_id=item.project_id).first() if item.project_id else None
    billing_job=FreshBooksProject.objects.filter(project_id=item.project_id).first() if item.project_id else None
    initial={'freshbooks_action':'create' if freshbooks_projects.ready() else 'skip','freshbooks_pricing':'fixed_price','mode':'existing' if binding or item.kind=='update' else 'new',
             'folder':binding.folder_id if binding else None,
             'title':item.project.name if item.project_id else item.project_name,
             'address':', '.join(filter(None,[item.project_street,item.project_city,item.project_state]))}
    form=FilingForm(request.POST if request.method=='POST' and request.POST.get('action')=='preview' else None,initial=initial)
    snapshot,signed=None,''
    error=''
    status=200
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='preview':
                if form.is_valid():
                    snapshot,signed=filing.preview(item=item,mode=form.cleaned_data['mode'],
                        folder_id=form.cleaned_data['folder'].pk if form.cleaned_data['folder'] else '',
                        title=form.cleaned_data['title'],address=form.cleaned_data['address'],actor=request.user,
                        freshbooks={'action':form.cleaned_data['freshbooks_action'] or 'skip',
                            'pricing':form.cleaned_data['freshbooks_pricing'] or 'fixed_price',
                            'amount':form.cleaned_data['freshbooks_amount'], 'remote_id':form.cleaned_data['freshbooks_project_id']})
                else:
                    status=400
            elif action=='approve':
                if request.POST.get('confirm')!='yes':
                    raise ValidationError('Confirm the destination shown in the preview.')
                filing.approve(item_id=item.pk,signed=request.POST.get('preview',''),actor=request.user)
                messages.success(request,'Filing approved. Transfers run in the background; originals stay available in the portal.')
                return redirect('workqueue:dropbox_review',item.pk)
            elif action in {'freshbooks_check','freshbooks_retry','freshbooks_link'}:
                if request.POST.get('confirm')!='yes':
                    raise ValidationError('Confirm the FreshBooks recovery action after reviewing its current status.')
                if not billing_job:
                    raise ValidationError('This project has no approved FreshBooks request.')
                freshbooks_projects.recover(billing_job.pk,request.user,action=action.split('_',1)[1],
                    remote_id=forms.IntegerField(required=False,min_value=1).clean(request.POST.get('remote_id')))
                messages.success(request,'FreshBooks project status updated. No invoice or client email was sent.')
                return redirect('workqueue:dropbox_review',item.pk)
            elif action=='refresh':
                filing.refresh_index()
                return redirect('workqueue:dropbox_review',item.pk)
            elif action in {'retry','reconcile'}:
                job=get_object_or_404(DropboxFiling,pk=forms.UUIDField().clean(request.POST.get('filing')),submission__work_item=item)
                if action=='retry':
                    filing.retry(job.pk,request.user)
                else:
                    if request.POST.get('confirm')!='yes':
                        raise ValidationError('Confirm that you reviewed the exact existing folder.')
                    filing.reconcile_folder(job.pk,request.POST.get('folder',''),request.user)
                messages.success(request,'The approved filing is queued for another attempt.')
                return redirect('workqueue:dropbox_review',item.pk)
            else:
                raise ValidationError('Choose a filing action.')
        except (ValidationError,provider.DropboxError,EditConflict,BillingError) as exc:
            error=error_text(exc)
            status=409 if isinstance(exc,EditConflict) else 400
    jobs=DropboxFiling.objects.filter(submission__work_item=item).select_related('destination__folder').prefetch_related('files__attachment')
    for job in jobs:
        job.recovery_folder=DropboxFolder.objects.filter(available=True,name=job.destination.intended_name).first() if not job.destination.folder_id else None
    pending=filing.pending_submissions(item)
    folders=DropboxFolder.objects.filter(available=True)
    q=request.GET.get('q','').strip()[:150]
    if q:
        folders=folders.filter(Q(name__icontains=q)|Q(number__icontains=q))
    return render(request,'workqueue/dropbox_review.html',{'item':item,'form':form,'snapshot':snapshot,'signed':signed,
        'jobs':jobs,'pending':pending,'connection':DropboxConnection.objects.filter(pk=1).first(),
        'folders':folders[:50],'q':q,'binding':binding,'error':error,'billing_job':billing_job,
        'freshbooks_ready':freshbooks_projects.ready(),'show_approval':bool(pending or (binding and not billing_job))},status=status)
