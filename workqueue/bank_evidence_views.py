from datetime import date, timedelta
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .billing_mail import require_owner
from .freshbooks_mirror import selected
from .freshbooks_mirror_data import signature
from .live_reconciliation import amount, month_start
from .pm_views import staff_view, editable, error_text
from .test_payment_views import private
from .services import EditConflict
from .bank_evidence_models import PayoutEvidenceImport, LivePayoutEvidence, BankStatementImport, BankStatementEntry
from .bank_evidence import (MAX_BYTES, import_payouts, bank_upload, uploaded_rows, unpack, map_bank,
    save_statement, payout_intact, entry_intact, trace_match, compatible, match_state, confirm_match, undo_match, void_statement)


class PayoutForm(forms.Form):
    month=forms.CharField(label='Payout month',widget=forms.TextInput(attrs={'type':'month'}))
    file=forms.FileField(label='Payout export CSV (All columns)')
    confirm=forms.BooleanField(label='This is a fresh, complete LIVE payout export from the Stripe dashboard linked inside this FreshBooks business, with all statuses.')
    def clean_month(self):
        value=self.cleaned_data['month'];month_start(value);return value


class BankForm(forms.Form):
    month=forms.CharField(label='Payout month to review',widget=forms.TextInput(attrs={'type':'month'}))
    bank_label=forms.CharField(max_length=60,label='Bank account label',help_text='For example, Business checking. Do not enter a full account number.')
    bank_last4=forms.RegexField(r'^[0-9]{4}$',max_length=4,label='Last four account digits')
    currency=forms.RegexField(r'^[A-Z]{3}$',max_length=3,initial='USD',label='Statement currency',help_text='Confirm the statement currency, such as USD. Currencies are never combined.')
    encoding=forms.ChoiceField(choices=[('utf-8-sig','UTF-8 (usual CSV export)'),('cp1252','Windows text')],label='Text encoding')
    header_row=forms.IntegerField(min_value=1,max_value=20,initial=1,label='CSV header row',help_text='Use 1 unless your export starts with summary or account information.')
    file=forms.FileField(label='Bank statement CSV (up to 1 MB)')
    def clean_month(self):
        value=self.cleaned_data['month'];month_start(value);return value


class MappingForm(forms.Form):
    payload=forms.CharField(widget=forms.HiddenInput)
    date_format=forms.ChoiceField(choices=[('us','MM/DD/YYYY'),('iso','YYYY-MM-DD'),('us_short','MM/DD/YY')],label='Date format')
    mode=forms.ChoiceField(choices=[('signed','One amount column: deposits positive, withdrawals negative'),('split','Separate deposit / credit and withdrawal / debit columns')],label='Amount format')
    date_column=forms.TypedChoiceField(coerce=int,label='Posted date column')
    description_column=forms.TypedChoiceField(coerce=int,label='Description column')
    amount_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Signed amount column (one-column format)')
    credit_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Deposit / credit column (separate-column format)')
    debit_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Withdrawal / debit column (separate-column format)')
    reference_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Bank reference / trace column (optional)')
    id_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Unique bank transaction ID column (optional)')
    currency_column=forms.TypedChoiceField(coerce=int,required=False,empty_value=None,label='Currency column (if supplied in CSV)')
    def __init__(self,*args,headers,**kwargs):
        super().__init__(*args,**kwargs)
        choices=[(i,header) for i,header in enumerate(headers)]
        for key in self.fields:
            if key.endswith('_column'):
                self.fields[key].choices=[('', 'Choose a column' if self.fields[key].required else 'Not used')]+choices


class MatchForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    entry=forms.ModelChoiceField(queryset=BankStatementEntry.objects.none(),label='Statement deposit')
    note=forms.CharField(required=False,max_length=500,widget=forms.Textarea(attrs={'rows':3}),label='Review note',
        help_text='Without an exact payout or bank trace reference, explain how you verified this deposit.')
    confirm=forms.BooleanField(label='I reviewed the statement deposit and verified that it belongs to this payout. A matching amount alone is not enough.')
    def __init__(self,*args,entries,**kwargs):
        super().__init__(*args,**kwargs);self.fields['entry'].queryset=entries
        self.fields['entry'].label_from_instance=lambda row:f"{row.posted_date} · {amount(row.amount)} {row.statement.currency} · {row.source['description'][:100]} · CSV row {row.line_number}"


def business_entries(connection):
    return BankStatementEntry.objects.filter(statement__account_id=connection.account_id,statement__business_id=connection.business_id,statement__revoked_at__isnull=True)


@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    require_owner(request.user);connection=selected();error='';status=200
    default=(timezone.localdate().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    try:start=month_start(request.GET.get('month',default))
    except ValidationError as exc:start=month_start(default);error=error_text(exc);status=400
    payout_form=PayoutForm(auto_id='payout_%s',initial={'month':start.strftime('%Y-%m')});bank_form=BankForm(auto_id='bank_%s',initial={'month':start.strftime('%Y-%m'),'currency':'USD','encoding':'utf-8-sig','header_row':1})
    if request.method=='POST':
        editable(request)
        try:
            action=request.POST.get('action')
            if action=='void':
                if not connection:raise ValidationError('Select the FreshBooks business first.')
                identifier=forms.UUIDField().clean(request.POST.get('statement'))
                get_object_or_404(BankStatementImport,pk=identifier,account_id=connection.account_id,business_id=connection.business_id)
                void_statement(actor=request.user,statement_id=identifier,reason=request.POST.get('reason',''))
                messages.success(request,'Statement import excluded from new matches; its evidence and prior review history were retained.')
                return redirect(f'{request.path}?month={start:%Y-%m}')
            if action=='payouts':
                payout_form=PayoutForm(request.POST,request.FILES,auto_id='payout_%s')
                if not payout_form.is_valid():raise ValidationError('Review the payout import fields.')
                data=payout_form.cleaned_data;file=data['file']
                batch=import_payouts(actor=request.user,content=file.read(MAX_BYTES+1),filename=file.name,month=data['month'])
                messages.success(request,'Payout source evidence saved. No deposit has been confirmed and no money moved.')
                return redirect(f"{request.path}?month={batch.month:%Y-%m}")
            if action=='bank':
                bank_form=BankForm(request.POST,request.FILES,auto_id='bank_%s')
                if not bank_form.is_valid():raise ValidationError('Review the bank CSV fields.')
                data=dict(bank_form.cleaned_data);file=data.pop('file')
                payload=bank_upload(actor=request.user,content=file.read(MAX_BYTES+1),filename=file.name,**data)
                context=unpack(payload,request.user,'mapping');rows=uploaded_rows(context);headers=rows[context['header_row']-1]
                form=MappingForm(headers=headers,initial={'payload':payload,'mode':'signed','date_format':'us'})
                return private(render(request,'workqueue/bank_statement_mapping.html',{'form':form,'headers':headers,'sample':rows[context['header_row']:context['header_row']+5],'context':context}))
            raise ValidationError('Choose a source import action.')
        except ValidationError as exc:error=error_text(exc);status=400
    rows=[];totals={};source_batch=None;statement_batches=[]
    if connection:
        source_batch=PayoutEvidenceImport.objects.filter(account_id=connection.account_id,business_id=connection.business_id,month=start).first()
        query=LivePayoutEvidence.objects.filter(account_id=connection.account_id,business_id=connection.business_id,source__initiated_date__startswith=start.strftime('%Y-%m')).select_related('source_import')
        for row in query:
            row.amount_display=amount(row.source['amount']);row.intact=payout_intact(row)
            row.deposit_eligible=row.source['type']=='bank_account' and row.source['amount']>0
            match=row.bank_matches.filter(revoked_at__isnull=True).select_related('payout__source_import','bank_entry__statement').first()
            row.bank_state=match_state(match) if match else 'unconfirmed'
            total=totals.setdefault(row.source['currency'],{'currency':row.source['currency'],'sent':0,'confirmed':0,'count':0,'matched':0,'review':0})
            total['count']+=1;total['review']+=int(not row.intact or row.bank_state=='review' or not row.deposit_eligible or row.source['status'] not in ('paid','pending','in_transit'))
            if row.intact and row.source['status']=='paid' and row.source['amount']>0:
                total['sent']+=row.source['amount']
            if row.bank_state=='confirmed':total['confirmed']+=row.source['amount'];total['matched']+=1
            rows.append(row)
        statement_batches=BankStatementImport.objects.filter(account_id=connection.account_id,business_id=connection.business_id,month=start)[:20]
    for total in totals.values():total['sent_display']=amount(total['sent']);total['confirmed_display']=amount(total['confirmed'])
    return private(render(request,'workqueue/bank_reconciliation.html',{'connection':connection,'month':start,'payout_form':payout_form,
        'bank_form':bank_form,'source_batch':source_batch,'payouts':rows,'totals':list(totals.values()),'statements':statement_batches,'error':error},status=status))


@staff_view
@require_http_methods(['POST'])
def mapping(request):
    require_owner(request.user);editable(request)
    try:
        context=unpack(request.POST.get('payload'),request.user,'mapping');rows=uploaded_rows(context);headers=rows[context['header_row']-1]
        form=MappingForm(request.POST,headers=headers)
        if not form.is_valid():raise ValidationError('Choose the statement columns and formats.')
        data=dict(form.cleaned_data);payload=data.pop('payload');preview=map_bank(actor=request.user,payload=payload,mapping=data)
        value=unpack(preview,request.user,'preview');source=value['source']
        for row in source['rows']:row['display']=amount(row['amount'])
        # Display-only formatting is not included in the signed source payload.
        return private(render(request,'workqueue/bank_statement_preview.html',{'context':value,'rows':source['rows'][:50],
            'count':len(source['rows']),'payload':preview,'duplicate_count':sum(row['duplicate'] for row in source['rows'])}))
    except ValidationError as exc:
        return private(render(request,'workqueue/bank_statement_error.html',{'error':error_text(exc)},status=400))


@staff_view
@require_http_methods(['POST'])
def save(request):
    require_owner(request.user);editable(request)
    try:batch=save_statement(actor=request.user,payload=request.POST.get('payload'),confirm=request.POST.get('confirm')=='on')
    except ValidationError as exc:return private(render(request,'workqueue/bank_statement_error.html',{'error':error_text(exc)},status=400))
    messages.success(request,'Statement deposit evidence saved. Review and confirm each payout match separately.')
    return redirect(f"/work-projects/billing/reconciliation/bank/?month={batch.month:%Y-%m}")


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,payout_id):
    require_owner(request.user);connection=selected()
    if not connection:return private(HttpResponse('Select your FreshBooks business first.',status=409))
    payout=get_object_or_404(LivePayoutEvidence.objects.select_related('source_import'),pk=payout_id,account_id=connection.account_id,business_id=connection.business_id)
    source=payout.source;error='';status=200
    entries=business_entries(connection).filter(statement__bank_last4=source['last4'],statement__currency=source['currency'],amount=source['amount'],
        posted_date__gte=date.fromisoformat(source['initiated_date']),posted_date__lte=date.fromisoformat(source['arrival_date'])+timedelta(days=31)).select_related('statement')
    form=MatchForm(entries=entries,initial={'version':payout.version})
    if request.method=='POST':
        editable(request)
        try:
            if request.POST.get('action')=='match':
                form=MatchForm(request.POST,entries=entries)
                if not form.is_valid():raise ValidationError('Choose a deposit, review it and confirm the match.')
                data=form.cleaned_data;confirm_match(actor=request.user,payout_id=payout.pk,entry_id=data['entry'].pk,
                    version=data['version'],confirm=data['confirm'],note=data['note'])
                messages.success(request,'Owner-reviewed bank CSV match recorded. This payout is a cash transfer, not another payment or revenue entry.')
            elif request.POST.get('action')=='undo':
                record=get_object_or_404(payout.bank_matches,pk=request.POST.get('match'))
                undo_match(actor=request.user,match_id=record.pk,reason=request.POST.get('reason',''))
                messages.success(request,'Match corrected. Its original evidence and review history were retained.')
            else:raise ValidationError('Choose a bank matching action.')
        except (ValidationError,EditConflict) as exc:error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=409 if isinstance(exc,EditConflict) else 400
        else:return redirect('workqueue:bank_payout_detail',payout.pk)
    candidates=list(entries[:50])
    for row in candidates:row.display=amount(row.amount);row.reference_match=trace_match(payout,row);row.intact=entry_intact(row)
    history=list(payout.bank_matches.select_related('payout__source_import','bank_entry__statement','created_by','revoked_by')[:30])
    for row in history:row.state=match_state(row)
    current=next((row for row in history if not row.revoked_at),None)
    return private(render(request,'workqueue/bank_payout_detail.html',{'payout':payout,'amount':amount(source['amount']),
        'intact':payout_intact(payout),'deposit_eligible':source['type']=='bank_account' and source['amount']>0,
        'form':form,'candidates':candidates,'current':current,'history':history,'error':error},status=status))
