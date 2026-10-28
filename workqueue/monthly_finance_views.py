import csv
import io
import uuid
from datetime import datetime, date
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .monthly_finance import report, month_dates, refresh_month, save_review, saved_report
from .monthly_finance_models import NativeFinancialReview
from .payment_finance import EASTERN
from .pm_views import staff_view, editable, error_text
from .services import EditConflict
from .test_payment_views import private


class MonthForm(forms.Form):
    month = forms.CharField(max_length=7, widget=forms.TextInput(attrs={'type':'month'}),label='Month')
    def clean_month(self):
        value=self.cleaned_data['month']; month_dates(value); return value


class RefreshForm(MonthForm):
    token=forms.UUIDField(widget=forms.HiddenInput)


class ReviewForm(RefreshForm):
    fingerprint=forms.RegexField(r'^[a-f0-9]{64}$',widget=forms.HiddenInput)
    note=forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':3}),label='Review note')
    confirm=forms.BooleanField(label='I reviewed this completed month. This saves test evidence; it does not certify bank receipt or lock accounting entries.')


def prepared(data):
    # Work on a copy: display strings are not part of immutable financial evidence.
    import copy
    data=copy.deepcopy(data)
    display_time=lambda value:datetime.fromisoformat(value).astimezone(EASTERN).strftime('%b %d, %Y %I:%M %p %Z')
    data['as_of_display']=display_time(data['as_of'])
    data['through_display']=date.fromisoformat(data['through']).strftime('%b %d, %Y')
    if data['source']:data['source']['captured_display']=display_time(data['source']['captured_at'])
    data['money']={key:f'{Decimal(value)/100:,.2f}' for key,value in data['totals'].items()}
    for group in ['stripe','ledger','receivables']:
        for row in data[group]:
            if 'occurred_at' in row:row['date_display']=display_time(row['occurred_at'])
            for key in ['amount','fee','net','paid','credits','due']:
                if key in row:row[key+'_display']=f'{Decimal(row[key])/100:,.2f}'
    return data


def csv_text(value):
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value


def export_csv(data):
    output=io.StringIO(newline='');writer=csv.writer(output)
    writer.writerow(['TEST ONLY - Monthly financial review',data['month'],'USD','America/New_York'])
    writer.writerow(['Generated',data['as_of'],'Through',data['through']])
    writer.writerow(['Scope','Native sandbox invoices and complete saved Stripe TEST balance activity. FreshBooks/live billing excluded.'])
    writer.writerow(['Meaning','Net Stripe activity is not profit, a tax report, or a closing bank/Stripe balance. Offline payment fees are not known.'])
    writer.writerow(['Fingerprint',data['fingerprint']])
    for issue in data['issues']:writer.writerow(['Review item',csv_text(issue)])
    writer.writerow(['SUMMARY','Amount USD'])
    for key,value in data['totals'].items():writer.writerow([key,f'{Decimal(value)/100:.2f}'])
    writer.writerow(['STRIPE ACTIVITY','Date','Type','Amount USD','Fees USD','Net USD','Invoice','Source','Availability'])
    for row in data['stripe']:
        writer.writerow([row['provider_id'],row['occurred_at'],row['kind'],*[f'{Decimal(row[k])/100:.2f}' for k in ['amount','fee','net']],
            csv_text(row['invoice']),row['source_id'],row['availability']])
    writer.writerow(['APP LEDGER','Date','Kind','Amount USD','Method','Stripe-linked'])
    for row in data['ledger']:writer.writerow([csv_text(row['invoice']),row['date'],row['kind'],f"{Decimal(row['amount'])/100:.2f}",row['method'],row['online']])
    writer.writerow(['RECEIVABLES AT CUTOFF','Client','Project','Due date','Invoice USD','Net paid USD','Credits USD','Due USD'])
    for row in data['receivables']:
        writer.writerow([csv_text(row['number']),csv_text(row['client']),csv_text(row['project']),row['due_date'],
            *[f'{Decimal(row[k])/100:.2f}' for k in ['amount','paid','credits','due']]])
    response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="TEST-monthly-review-{data["month"]}.csv"'
    return private(response)


@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    default=timezone.now().astimezone(EASTERN).strftime('%Y-%m')
    form=MonthForm(request.POST if request.method=='POST' else request.GET or {'month':default})
    error='';status=200;data=None;history=[];last=None
    if not form.is_valid():
        return private(render(request,'workqueue/monthly_finance.html',{'form':form,'error':'Choose a valid month.'},status=400))
    month=form.cleaned_data['month']
    if request.method=='POST':
        editable(request)
        action=request.POST.get('action')
        action_form=ReviewForm(request.POST) if action=='review' else RefreshForm(request.POST)
        try:
            if action not in {'refresh','review'} or not action_form.is_valid():
                raise ValidationError('Review the month and required fields.')
            if action=='refresh':
                refresh_month(actor=request.user,**action_form.cleaned_data)
                messages.success(request,'Complete monthly Stripe test activity saved. No payments, invoices or transfers were changed.')
            else:
                row=save_review(actor=request.user,**action_form.cleaned_data)
                messages.success(request,'Completed-month test review saved. Future changes will be flagged; original evidence is retained.')
                return redirect('workqueue:monthly_finance_detail',row.pk)
        except (ValidationError,EditConflict) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409 if isinstance(exc,EditConflict) else 400
        else:
            from django.urls import reverse
            return redirect(reverse('workqueue:monthly_finance')+'?month='+month)
    try:
        data=report(month)
        if request.GET.get('download')=='csv':return export_csv(data)
        start,_=month_dates(month)
        history=list(NativeFinancialReview.objects.filter(month_id=start).select_related('actor')[:20])
        last=history[0] if history else None
        matches=bool(last and saved_report(last)['fingerprint']==data['fingerprint'])
    except ValidationError as exc:
        error=error_text(exc);status=400;data=None;matches=False
    return private(render(request,'workqueue/monthly_finance.html',{
        'form':form,'error':error,'data':prepared(data) if data else None,'month':month,'history':history,'last':last,
        'matches':matches,'refresh_form':RefreshForm(initial={'month':month,'token':uuid.uuid4()}),
        'review_form':ReviewForm(request.POST if request.method=='POST' and request.POST.get('action')=='review' else None,
            initial={'month':month,'token':uuid.uuid4(),'fingerprint':data['fingerprint'] if data else ''}),
        'can_review':bool(data and not data['issues'])},status=status))


@staff_view
@require_http_methods(['GET'])
def detail(request,review_id):
    row=get_object_or_404(NativeFinancialReview.objects.select_related('actor'),pk=review_id)
    try:
        data=saved_report(row)
        if request.GET.get('download')=='csv':return export_csv(data)
    except ValidationError as exc:
        return private(HttpResponse(error_text(exc),status=409,content_type='text/plain'))
    current_issue=''
    try:matches=data['fingerprint']==report(row.month_id)['fingerprint']
    except ValidationError:
        matches=False;current_issue='Current source figures could not be verified. The original saved review below remains intact.'
    return private(render(request,'workqueue/monthly_finance.html',{'data':prepared(data),'saved':row,
        'month':data['month'],'matches':matches,'error':current_issue}))
