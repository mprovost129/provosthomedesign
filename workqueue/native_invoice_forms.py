from decimal import Decimal, ROUND_HALF_UP
from django import forms
from .native_invoice_models import NativeInvoiceEntry


class ActionForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1,widget=forms.HiddenInput)


class PlanForm(ActionForm):
    discount = forms.DecimalField(min_value=0,max_value=Decimal('999999999.99'),max_digits=11,decimal_places=2,initial=0,label='Fixed discount (USD)')
    tax_percent = forms.DecimalField(min_value=0,max_value=100,max_digits=5,decimal_places=2,initial=0,label='Tax percentage',help_text='Optional. Applied once to the whole discounted plan; leave 0 for no tax.')
    confirm = forms.BooleanField(label='Create a test billing plan only. FreshBooks remains my billing system.')


class ClosePlanForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    note = forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':2}),label='Reason for closing the test plan')
    confirm = forms.BooleanField(label='Close this test plan and allow a replacement. Keep its invoice history.')


class IssueForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    stage = forms.ChoiceField(choices=[('deposit','Deposit'),('progress','Progress'),('final','Final / remaining')],label='Billing stage')
    description = forms.CharField(max_length=200,label='Invoice description')
    basis = forms.ChoiceField(choices=[('amount','Dollar amount'),('percent','Percentage of full plan'),('remaining','All remaining unbilled work')],label='How much to bill')
    value = forms.DecimalField(required=False,min_value=Decimal('.01'),max_value=Decimal('999999999.99'),max_digits=11,decimal_places=2,label='Amount or percentage',help_text='For a percentage, enter 25 for 25%. Leave empty when billing all remaining work.')
    issue_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    confirm = forms.BooleanField(label='Create a numbered test invoice only. Do not email or charge the client.')

    def __init__(self,*args,plan,remaining,**kwargs):
        self.plan,self.remaining = plan,remaining
        super().__init__(*args,**kwargs)

    def clean(self):
        data=super().clean();basis=data.get('basis');value=data.get('value')
        if basis=='remaining':
            if value is not None:
                self.add_error('value','Leave this empty when billing the remaining amount.')
            data['amount']=self.remaining
        elif basis in {'amount','percent'}:
            if value is None:self.add_error('value','Enter an amount or percentage.')
            elif basis=='percent' and value>100:self.add_error('value','The percentage cannot exceed 100.')
            else:data['amount']=(self.plan.total*value/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if basis=='percent' else value
        return data


class EntryForm(ActionForm):
    kind = forms.ChoiceField(choices=[('payment','Record a test payment'),('credit','Apply a test credit')],label='Action')
    amount = forms.DecimalField(min_value=Decimal('.01'),max_value=Decimal('999999999.99'),max_digits=11,decimal_places=2,label='Amount (USD)')
    entry_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Payment / credit date')
    method = forms.ChoiceField(required=False,choices=[('','No payment method (credit only)'),*NativeInvoiceEntry.Method.choices],label='Payment method')
    reference = forms.CharField(required=False,max_length=100,label='Check / transaction reference',help_text='Never enter card or bank account numbers.')
    note = forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':3}),label='Internal note / credit reason')
    confirm = forms.BooleanField(label='Record this test entry only. This does not process a payment or refund.')


class ReverseForm(ActionForm):
    entry = forms.ChoiceField(label='Entry to reverse')
    note = forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':2}),label='Reason for reversing the record')
    confirm = forms.BooleanField(label='Reverse the test record only. No money will be refunded.')

    def __init__(self,*args,entries,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['entry'].choices=[(str(entry.pk),f'{entry.get_kind_display()} · ${entry.amount} · {entry.date} · {entry.reference or entry.credit_number or "No reference"}') for entry in entries]


class VoidForm(ActionForm):
    note = forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':2}),label='Reason for voiding')
    confirm = forms.BooleanField(label='Void this test invoice and release its amount for billing again.')
