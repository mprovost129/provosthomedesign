import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_GET
from .models import WorkProject, WorkItem
from .time_billing_models import TimeBillingBatch, TimeBillingRate
from .time_billing import eligible,billing_clients,preview,queue_time_invoice,release_batch,fixed_scope
from .pm_views import staff_view,editable,error_text,query_uuid
from .services import EditConflict
from . import freshbooks
from .views import staff_preview
from .reporting import hours

SALT='workqueue.hourly-invoice.v1'


class InvoiceForm(forms.Form):
    client=forms.ModelChoiceField(queryset=None,label='Billing company / client')
    rate=forms.DecimalField(min_value=Decimal('.01'),max_value=Decimal('999999.99'),decimal_places=2,max_digits=8,label='Hourly rate (USD)')
    notes=forms.CharField(max_length=2000,required=False,widget=forms.Textarea(attrs={'rows':3}),label='Invoice note visible to client')
    remember=forms.BooleanField(required=False,initial=True,label='Use this rate for future hourly invoices on this project')
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs);self.fields['client'].queryset=billing_clients(project)


@staff_view
@require_GET
def ready(request):
    q=request.GET.get('q','').strip()[:200]
    jobs=WorkItem.objects.filter(project_id=OuterRef('pk'))
    unbilled=eligible().filter(project_id=OuterRef('pk'))
    projects=WorkProject.objects.annotate(visible=Exists(jobs),unbilled=Exists(unbilled)).filter(visible=True,unbilled=True)
    if q:
        from django.db.models import Q
        projects=projects.filter(Q(name__icontains=q)|Q(street__icontains=q)|Q(work_items__client_contact__client__name__icontains=q)).distinct()
    page=Paginator(projects.order_by('name','pk'),30).get_page(request.GET.get('page'))
    rows=[{'project':p,'hours':hours(eligible(p).aggregate(seconds=Sum('duration_seconds'))['seconds'] or 0)} for p in page]
    return render(request,'workqueue/ready_to_invoice.html',{'page':page,'rows':rows,'q':q})


@staff_view
@require_http_methods(['GET','POST'])
def project_billing(request,project_id):
    project=get_object_or_404(WorkProject,pk=project_id)
    can_change=request.user.has_perm('workqueue.change_workitem') and not staff_preview(request)
    entries=list(eligible(project).order_by('work_date','pk')[:200])
    for entry in entries:
        entry.public_description=(request.POST.get('description_'+str(entry.pk),entry.description or entry.get_category_display()))[:2000]
    initial={'rate':TimeBillingRate.objects.filter(project=project).values_list('rate',flat=True).first()}
    clients=billing_clients(project)
    if clients.count()==1:initial['client']=clients.first()
    form=InvoiceForm(request.POST or None,project=project,initial=initial)
    status,review,signature=200,None,''
    selected=set(request.POST.getlist('entries'))
    if request.method=='POST':
        editable(request);status=400
        try:
            action=request.POST.get('action')
            if action=='preview':
                if form.is_valid():
                    ids=request.POST.getlist('entries')
                    if not 1<=len(ids)<=50 or len(ids)!=len(set(ids)):raise ValidationError('Select 1–50 different completed entries.')
                    clean=[query_uuid(value) for value in ids]
                    chosen=list(eligible(project).filter(pk__in=clean))
                    if len(chosen)!=len(clean):raise EditConflict('Some selected time is no longer available. Reload before invoicing it.')
                    review=preview(project,form.cleaned_data['client'],chosen,form.cleaned_data['rate'],
                        {str(e.pk):request.POST.get('description_'+str(e.pk),'') for e in chosen},form.cleaned_data['notes'])
                    signature=signing.dumps({'snapshot':review,'batch':str(uuid.uuid4()),'actor':request.user.pk,
                                            'remember':form.cleaned_data['remember']},salt=SALT)
                    status=200
            elif action=='confirm':
                if request.POST.get('confirm')!='yes':raise ValidationError('Confirm the reviewed invoice draft request.')
                try:signed=signing.loads(request.POST.get('signature',''),salt=SALT,max_age=1800)
                except signing.BadSignature:raise ValidationError('The invoice preview expired. Prepare a new preview.') from None
                if signed['actor']!=request.user.pk or signed['snapshot']['project']!=str(project.pk):raise ValidationError('Use your own preview for this project.')
                batch=queue_time_invoice(signed['snapshot'],uuid.UUID(signed['batch']),request.user,
                    remember=signed['remember'],additional=request.POST.get('additional')=='yes')
                messages.success(request,'Draft requested. Selected time is reserved; review and send the invoice in FreshBooks when it is ready.')
                return redirect('workqueue:time_bill_detail',batch.pk)
            else:raise ValidationError('Choose preview or confirm.')
        except (freshbooks.BillingError,ValidationError,EditConflict,ValueError,KeyError) as exc:
            form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
    return render(request,'workqueue/project_billing.html',{'project':project,'entries':entries,'form':form,'review':review,
        'signature':signature,'selected':selected,'can_change':can_change,'fixed':bool(fixed_scope(project)),
        'batches':project.time_bills.select_related('client','invoice').defer('snapshot').order_by('-created_at')[:30]},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def batch_detail(request,batch_id):
    batch=get_object_or_404(TimeBillingBatch.objects.select_related('project','client','invoice'),pk=batch_id)
    can_change=request.user.has_perm('workqueue.change_workitem') and not staff_preview(request)
    if request.method=='POST':
        editable(request)
        try:
            action=request.POST.get('action')
            if request.POST.get('confirm')!='yes':raise ValidationError('Confirm the requested billing action.')
            if action=='release':release_batch(batch.pk,request.user)
            elif action=='refresh':
                if batch.invoice.state in ('queued','creating','released'):raise ValidationError('Wait for the draft to finish, or review its released reservation.')
                freshbooks.sync_invoice(batch.invoice)
            elif action=='retry':freshbooks.retry_rejected_invoice(batch.invoice_id,request.user,confirm=True)
            else:raise ValidationError('Choose a billing action.')
            messages.success(request,'Billing action saved.')
        except (freshbooks.BillingError,ValidationError,EditConflict) as exc:messages.error(request,error_text(exc))
        return redirect('workqueue:time_bill_detail',batch.pk)
    url=f'https://my.freshbooks.com/#/invoice/{batch.invoice.account_id}-{batch.invoice.invoice_id}' if batch.invoice.invoice_id else ''
    return render(request,'workqueue/time_bill_detail.html',{'batch':batch,'invoice':batch.invoice,'can_change':can_change,'freshbooks_url':url})
