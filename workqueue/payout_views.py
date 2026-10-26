import uuid
from decimal import Decimal
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .pm_views import staff_view,editable,error_text
from .billing_mail import require_owner
from .payment_finance import cents,EASTERN
from .test_payment_views import private
from .test_payment_gateway import GatewayUnavailable
from .payout_models import NativePayout
from .payout_reconciliation import (settings_record,save_settings,discover,lookup,reconcile,stripe_balanced,local_links,
    active_confirmation,bank_state,confirm_bank,undo_bank,MESSAGES,PayoutIssue)
from .services import EditConflict

class SettingsForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    enabled=forms.BooleanField(required=False,label='Enable read-only Stripe sandbox payout checks')
class LookupForm(forms.Form):
    provider_id=forms.RegexField(r'^po_[A-Za-z0-9]{1,220}$',max_length=224,label='Stripe payout reference',help_text='An exact po_ reference. Useful for payouts older than the recent 90-day discovery window.')
class FilterForm(forms.Form):
    start=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Arrival from')
    end=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Through')
    q=forms.CharField(required=False,max_length=150,label='Payout reference')
    state=forms.ChoiceField(required=False,choices=[('','All payouts'),('unconfirmed','Bank receipt not confirmed'),('attention','Needs attention')])
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and (data['start']>data['end'] or (data['end']-data['start']).days>366):raise ValidationError('Choose up to one year, from start through end date.')
        return data
class BankForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    token=forms.UUIDField(widget=forms.HiddenInput)
    amount=forms.DecimalField(max_digits=14,decimal_places=2,min_value=Decimal('.01'),label='Deposit amount (USD)')
    deposit_date=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Bank statement date')
    reference=forms.CharField(max_length=100,label='Statement reference',help_text='Use a short reference; no bank account numbers are needed.')
    confirm=forms.BooleanField(label='I checked this matching deposit. This is a fictional sandbox bank confirmation; no real money moved.')
class UndoForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':3}),label='Reason for correction')

@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    config=settings_record();status=200;error='';lookup_form=LookupForm()
    settings_form=SettingsForm(initial={'version':config.version,'enabled':config.enabled})
    if request.method=='POST':
        editable(request)
        try:
            if request.POST.get('action')=='settings':
                require_owner(request.user);settings_form=SettingsForm(request.POST)
                if not settings_form.is_valid():raise ValidationError('Review the payout settings fields.')
                save_settings(actor=request.user,**settings_form.cleaned_data)
                messages.success(request,'Read-only sandbox payout settings saved.')
            elif request.POST.get('action')=='discover':
                rows=discover();messages.success(request,f'Checked the complete recent 90-day payout list: {len(rows)} payouts discovered. Their transaction math is checked separately.')
            elif request.POST.get('action')=='lookup':
                lookup_form=LookupForm(request.POST)
                if not lookup_form.is_valid():raise ValidationError('Enter the exact Stripe payout reference, beginning po_.')
                row=lookup(**lookup_form.cleaned_data);return redirect('workqueue:payout_detail',row.pk)
            else:raise ValidationError('Choose a payout action.')
        except (ValidationError,GatewayUnavailable,EditConflict,PayoutIssue) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409 if isinstance(exc,EditConflict) else 400
        else:return redirect('workqueue:payouts')
    today=timezone.now().astimezone(EASTERN).date()
    form=FilterForm(request.GET or {'start':(today-timedelta(days=30)).isoformat(),'end':(today+timedelta(days=14)).isoformat()})
    rows=[];totals={'paid':0,'balanced':0,'bank_confirmed':0};attention=0;unconfirmed=0
    if form.is_valid():
        qs=NativePayout.objects.filter(arrival_date__gte=form.cleaned_data['start'],arrival_date__lte=form.cleaned_data['end'])
        if form.cleaned_data['q']:qs=qs.filter(provider_id__icontains=form.cleaned_data['q'])
        for row in qs.prefetch_related('transactions','bank_confirmations'):
            row.amount_display=cents(row.amount) if row.currency=='usd' else None
            row.balanced=stripe_balanced(row);row.bank_state=bank_state(row)
            needs=row.state in {'pending','review','unsupported'} or row.status in {'failed','canceled'} or row.bank_state=='review'
            if form.cleaned_data['state']=='unconfirmed' and row.bank_state!='unconfirmed':continue
            if form.cleaned_data['state']=='attention' and not needs:continue
            rows.append(row);attention+=bool(needs);unconfirmed+=row.bank_state=='unconfirmed'
            if row.currency=='usd' and row.status=='paid':totals['paid']+=row.amount
            if row.balanced:totals['balanced']+=row.amount
            if row.bank_state=='confirmed':totals['bank_confirmed']+=row.amount
    elif request.method=='GET':status=400
    params=request.GET.copy();params.pop('page',None)
    if form.is_valid():params.update({'start':form.cleaned_data['start'].isoformat(),'end':form.cleaned_data['end'].isoformat()})
    return private(render(request,'workqueue/payouts.html',{'config':settings_record(),'settings_form':settings_form,'lookup_form':lookup_form,
      'form':form,'page':Paginator(rows,25).get_page(request.GET.get('page')),'query_string':params.urlencode(),'error':error,
      'totals':{k:cents(v) for k,v in totals.items()},'attention':attention,'unconfirmed':unconfirmed,'count':len(rows)},status=status))

@staff_view
@require_http_methods(['GET','POST'])
def detail(request,payout_id):
    row=get_object_or_404(NativePayout,pk=payout_id);error='';status=200
    today=timezone.now().astimezone(EASTERN).date()
    bank_form=BankForm(initial={'version':row.version,'token':uuid.uuid4(),'amount':cents(row.amount),'deposit_date':min(row.arrival_date,today)})
    undo_form=UndoForm(initial={'version':row.version})
    if request.method=='POST':
        editable(request)
        try:
            if request.POST.get('action')=='refresh':reconcile(row.pk);messages.info(request,'Read-only Stripe payout check completed. Review the reconciliation status below.')
            elif request.POST.get('action')=='confirm':
                require_owner(request.user);bank_form=BankForm(request.POST)
                if not bank_form.is_valid():raise ValidationError('Review the bank confirmation fields.')
                confirm_bank(payout_id=row.pk,actor=request.user,**bank_form.cleaned_data)
                messages.success(request,'Fictional sandbox bank receipt recorded. No invoice, payment or revenue entry was created.')
            elif request.POST.get('action')=='undo':
                require_owner(request.user);undo_form=UndoForm(request.POST)
                if not undo_form.is_valid():raise ValidationError('Explain the bank confirmation correction.')
                undo_bank(payout_id=row.pk,actor=request.user,**undo_form.cleaned_data);messages.success(request,'Bank confirmation undone; its original history was retained.')
            else:raise ValidationError('Choose a payout action.')
        except (ValidationError,GatewayUnavailable,EditConflict,PayoutIssue) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409 if isinstance(exc,EditConflict) else 400
        else:return redirect('workqueue:payout_detail',row.pk)
    row.refresh_from_db();links=local_links(row);balanced=stripe_balanced(row);bank=active_confirmation(row)
    raw=sum(item.amount for item in links['entries']);fees=sum(item.fee for item in links['entries']);net=sum(item.net for item in links['entries'])
    standalone=sum(item.amount for item in links['entries'] if item.kind in {'stripe_fee','stripe_fx_fee','tax_fee'})
    payments=sum(item.amount for item in links['entries'] if item.kind in {'charge','payment'})
    refunds=sum(item.amount for item in links['entries'] if item.kind in {'refund','payment_refund','refund_failure','payment_failure_refund'})
    other=raw-standalone-payments-refunds
    if bank:bank.amount_display=cents(bank.amount)
    confirmations=list(row.bank_confirmations.select_related('actor','revoked_by').all())
    for item in confirmations:item.amount_display=cents(item.amount)
    return private(render(request,'workqueue/payout_detail.html',{'payout':row,'config':settings_record(),'error':error,'balanced':balanced,
      'confirmations':confirmations,'bank':bank,'bank_state':bank_state(row),'bank_form':bank_form,'undo_form':undo_form,'links':links,'issue':MESSAGES.get(row.issue,''),
      'amount':cents(row.amount) if row.currency=='usd' else None,'transaction_gross':cents(raw),'transaction_fees':cents(fees),
      'payment_gross':cents(payments),'refund_movements':cents(refunds),'standalone_movements':cents(standalone),
      'other_movements':cents(other),'all_payout_costs':cents(fees-standalone+(row.debit_fee or 0)),
      'transaction_net':cents(net),'payout_fee':cents(row.debit_fee) if row.debit_fee is not None else None,
      'can_confirm':balanced and not links['local_issues'] and row.checked_at and timezone.now()-row.checked_at<=timedelta(hours=1)},status=status))
