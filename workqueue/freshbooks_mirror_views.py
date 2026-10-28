from decimal import Decimal
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .pm_views import staff_view,editable,error_text
from .test_payment_views import private
from .services import EditConflict
from .models import Client
from .freshbooks_mirror_models import KINDS,FreshBooksMirrorJob,FreshBooksMirrorRecord
from .freshbooks_mirror_data import signature
from .freshbooks_mirror import selected,config,available,save_settings,request_sync,link_client


class SettingsForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    enabled=forms.BooleanField(required=False,label='Automatically mirror FreshBooks into this app')
    include_estimates=forms.BooleanField(required=False,label='Include FreshBooks estimates (requires read-only estimate access)')


class LinkForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    client=forms.ModelChoiceField(queryset=Client.objects.none(),label='Existing CRM client')
    confirm=forms.BooleanField(label='I checked the company and billing identity. Link this source to the selected CRM client.')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['client'].queryset=Client.objects.filter(merged_into__isnull=True).order_by('name')


def presentation(record):
    record.intact=signature(record.source)==record.source_hash
    record.amount_display=None;record.currency=''
    if record.intact and record.kind in ('invoices','payments','estimates'):
        record.amount_display=f"{Decimal(record.source['amount']):,.2f}";record.currency=record.source['currency']
    record.stale=record.verified_at<timezone.now()-timedelta(hours=24)
    return record


@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    connection=selected();settings=config(connection) if connection else None
    error='';status=200
    form=SettingsForm(initial={'version':settings.version if settings else 1,'enabled':bool(settings and settings.enabled),
        'include_estimates':bool(settings and settings.include_estimates)})
    if request.method=='POST':
        editable(request)
        try:
            if request.POST.get('action')=='settings':
                form=SettingsForm(request.POST)
                if not form.is_valid():raise ValidationError('Review the mirror settings.')
                save_settings(actor=request.user,**form.cleaned_data)
                started=request_sync(actor=request.user) if form.cleaned_data['enabled'] else 0
                messages.success(request,f'Mirror settings saved. {started} source checks queued. FreshBooks billing was not changed.')
            elif request.POST.get('action')=='sync':
                count=request_sync(actor=request.user)
                messages.info(request,f'{count} source checks queued. Existing checks continue in the background; repeated requests do not duplicate records.')
            else:raise ValidationError('Choose a mirror action.')
        except (ValidationError,EditConflict) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409 if isinstance(exc,EditConflict) else 400
        else:return redirect('workqueue:freshbooks_mirror')
    kind=request.GET.get('kind','invoices')
    if kind not in dict(KINDS):kind='invoices'
    query=request.GET.get('q','').strip()[:200];state=request.GET.get('state','all')
    rows=FreshBooksMirrorRecord.objects.filter(account_id=connection.account_id,kind=kind) if connection else FreshBooksMirrorRecord.objects.none()
    if request.GET.get('client'):
        try:rows=rows.filter(client_id=forms.UUIDField().clean(request.GET['client']))
        except ValidationError:rows=rows.none()
    if query:
        match=Q(title__icontains=query)|Q(client__name__icontains=query)|Q(source__email__icontains=query)|Q(source__client_name__icontains=query)|Q(source__description__icontains=query)
        if query.isdecimal() and len(query)<16:match|=Q(provider_id=int(query))
        rows=rows.filter(match)
    if state=='attention':rows=rows.filter(~Q(issues=[])|Q(missing=True))
    elif state=='archived':rows=rows.filter(Q(source__visibility=2)|Q(source__active=False))
    elif state!='all':state='all'
    sort=['-source__issue_date','-provider_id'] if kind in ('invoices','estimates') else ['-source__date','-provider_id'] if kind=='payments' else ['title','provider_id']
    page=Paginator(rows.select_related('client','project').defer('applied_fields').order_by(*sort),30).get_page(request.GET.get('page'))
    for row in page:presentation(row)
    jobs={j.kind:j for j in FreshBooksMirrorJob.objects.filter(account_id=connection.account_id)} if connection else {}
    coverage=[{'kind':key,'label':label,'available':available(connection,key),'job':jobs.get(key),
        'included':key!='estimates' or bool(settings and settings.include_estimates)} for key,label in KINDS]
    params=request.GET.copy();params.pop('page',None);params['kind']=kind
    return private(render(request,'workqueue/freshbooks_mirror.html',{'connection':connection,'settings':settings,'settings_form':form,
        'error':error,'page':page,'kind':kind,'kind_label':dict(KINDS)[kind],'query':query,'state':state,'coverage':coverage,
        'job':jobs.get(kind),'page_query':params.urlencode(),'estimate_scope':available(connection,'estimates')},status=status))


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,record_id):
    connection=selected()
    if not connection:return private(HttpResponse('Select the FreshBooks business to view its saved source records.',status=409))
    record=get_object_or_404(FreshBooksMirrorRecord.objects.select_related('client','project'),pk=record_id,account_id=connection.account_id)
    source=record.source;stamp=record.source_hash;historical=None
    if request.method=='GET' and request.GET.get('revision'):
        try:sequence=forms.IntegerField(min_value=1).clean(request.GET['revision'])
        except ValidationError:return private(HttpResponse('Choose a valid source revision.',status=400))
        historical=get_object_or_404(record.history,sequence=sequence);source=historical.source;stamp=historical.source_hash
    if signature(source)!=stamp:return private(HttpResponse('Saved source integrity needs review. Refresh FreshBooks before using this record.',status=409))
    form=LinkForm(initial={'version':record.version});status=200
    if request.method=='POST':
        editable(request);form=LinkForm(request.POST);status=400
        if record.kind=='clients' and form.is_valid():
            try:link_client(record_id=record.pk,client_id=form.cleaned_data['client'].pk,version=form.cleaned_data['version'],actor=request.user)
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc) if isinstance(exc,ValidationError) else str(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'CRM billing identity linked. No FreshBooks data was changed.');return redirect('workqueue:freshbooks_mirror_detail',record.pk)
        elif record.kind!='clients':form.add_error(None,'Only client source identities can be linked here.')
    related=FreshBooksMirrorRecord.objects.filter(account_id=record.account_id)
    if record.kind=='clients':related=related.filter(customer_id=record.provider_id).exclude(pk=record.pk)
    elif record.kind=='invoices':related=related.filter(kind='payments',provider_invoice_id=record.provider_id)
    else:related=related.none()
    related=list(related[:30])
    for row in related:presentation(row)
    return private(render(request,'workqueue/freshbooks_mirror_detail.html',{'record':presentation(record),'data':source,'historical':historical,
        'form':form,'related':related,'history':record.history.defer('source').all()[:20],
        'coverage':FreshBooksMirrorJob.objects.filter(account_id=record.account_id,kind=record.kind).first()},status=status))
