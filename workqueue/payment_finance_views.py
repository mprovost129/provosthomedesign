from datetime import date
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .pm_views import staff_view,editable,error_text
from .billing_mail import require_owner
from .payment_finance import settings_record,save_settings,report,reconcile,current,MESSAGES,cents,EASTERN
from .test_payment_models import NativePaymentAttempt
from .test_payment_gateway import GatewayUnavailable,configuration
from .test_payment_views import private
from .services import EditConflict

class FilterForm(forms.Form):
    start=forms.DateField(label='From',widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='Through',widget=forms.DateInput(attrs={'type':'date'}))
    q=forms.CharField(label='Invoice, client or project',required=False,max_length=150)
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end'):
            if data['start']>data['end'] or (data['end']-data['start']).days>366:
                raise ValidationError('Choose a date range of up to one year.')
        return data

class SettingsForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    enabled=forms.BooleanField(required=False,label='Enable read-only Stripe sandbox fee checks')

@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    config=settings_record();error='';status=200
    settings_form=SettingsForm(initial={'version':config.version,'enabled':config.enabled})
    if request.method=='POST':
        editable(request);require_owner(request.user);settings_form=SettingsForm(request.POST)
        if settings_form.is_valid():
            try:save_settings(actor=request.user,**settings_form.cleaned_data)
            except (ValidationError,EditConflict) as exc:
                error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409
            else:
                messages.success(request,'Read-only sandbox fee settings saved. No payment, refund or email was created.')
                return redirect('workqueue:payment_finance')
        else:status=400
    today=timezone.now().astimezone(EASTERN).date()
    form=FilterForm(request.GET if request.GET else {'start':today.replace(day=1).isoformat(),'end':today.isoformat()})
    result=None;history=None;entries=None
    if form.is_valid():
        result=report(form.cleaned_data['start'],form.cleaned_data['end'],form.cleaned_data['q'])
        history=Paginator(result['history'],25).get_page(request.GET.get('page'))
        entries=Paginator(result['entries'],50).get_page(request.GET.get('transactions'))
    elif request.method=='GET':status=400
    params=request.GET.copy();params.pop('page',None);params.pop('transactions',None)
    if form.is_valid():
        params.update({'start':form.cleaned_data['start'].isoformat(),'end':form.cleaned_data['end'].isoformat()})
    if entries:
        for entry in entries:
            entry.amount_display=cents(entry.amount);entry.fee_display=cents(entry.fee);entry.net_display=cents(entry.net)
    return private(render(request,'workqueue/payment_finance.html',{'form':form,'settings_form':settings_form,
        'finance_config':config,'config':configuration(),'result':result,'history':history,'entries':entries,
        'query_string':params.urlencode(),'error':error},status=status))

@staff_view
@require_http_methods(['GET','POST'])
def detail(request,attempt_id):
    attempt=get_object_or_404(NativePaymentAttempt.objects.select_related('invoice','finance'),pk=attempt_id)
    error='';status=200
    if request.method=='POST':
        editable(request)
        try:row=reconcile(attempt.pk)
        except (ValidationError,GatewayUnavailable) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=400
        else:
            messages.info(request,'Actual Stripe fee history reconciled.' if row.state=='verified' else MESSAGES.get(row.issue,'Fee history needs review.'))
            return redirect('workqueue:payment_finance_detail',attempt.pk)
    row=getattr(attempt,'finance',None);ok=bool(row and current(row))
    entries=list(row.entries.all()) if row else []
    for entry in entries:
        entry.amount_display=cents(entry.amount);entry.fee_display=cents(entry.fee);entry.net_display=cents(entry.net)
        for value in entry.fee_details:value['display']=cents(value['amount'])
    total={key:cents(sum(getattr(e,key) for e in entries)) for key in ['amount','fee','net']}
    return private(render(request,'workqueue/payment_finance_detail.html',{'attempt':attempt,'finance':row,'current':ok,
        'entries':entries,'totals':total,'issue':MESSAGES.get(row.issue,'Accounting changed after the last fee check. Refresh to reconcile.') if row and not ok else '',
        'finance_config':settings_record(),'error':error},status=status))
