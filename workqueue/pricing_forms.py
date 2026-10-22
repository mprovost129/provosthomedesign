import uuid
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from .models import Client, WorkProject
from .pricing_models import PricingRateVersion
from .pricing import SERVICES, RATE_KEYS, CATEGORIES, latest_card, calculate, check_assignment


class PricingClientChoice(forms.ModelChoiceField):
    def label_from_instance(self, client):
        category = client.get_pricing_category_display() if client.pricing_category else 'Not classified'
        return f'{client.name} — {category}'


class PricingForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    title = forms.CharField(max_length=200, label='Calculation name')
    client = PricingClientChoice(queryset=Client.objects.none(), required=False, label='Client')
    project = forms.ModelChoiceField(queryset=WorkProject.objects.none(), required=False, label='Project')
    category = forms.ChoiceField(choices=[('', 'Choose a pricing category'), *CATEGORIES], label='Pricing category')
    rate_version = forms.ModelChoiceField(queryset=PricingRateVersion.objects.all(), required=False, label='Rate card / version')
    mode = forms.ChoiceField(choices=[('area','Square-foot pricing'),('hourly','Hourly estimate')], label='Calculation method')
    project_review = forms.ChoiceField(choices=[('standard','Standard single-structure project'),
        ('custom','Multi-unit or other project requiring individual review')], label='Project review')
    rate_reason = forms.CharField(required=False, max_length=2000, label='Private notes for one-time rate changes', widget=forms.Textarea(attrs={'rows':2}))
    custom_description = forms.CharField(required=False, max_length=500, label='Additional fixed-price service')
    custom_fee = forms.DecimalField(required=False, max_digits=10, decimal_places=2, min_value=0, label='Additional service price ($)')
    override_total = forms.DecimalField(required=False, max_digits=11, decimal_places=2, min_value=0, label='Quoted fixed fee override ($)')
    override_reason = forms.CharField(required=False, max_length=2000, label='Private reason for the quoted fee override', widget=forms.Textarea(attrs={'rows':2}))
    internal_notes = forms.CharField(required=False, max_length=5000, label='Private estimate notes', widget=forms.Textarea(attrs={'rows':3}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['client'].queryset = Client.objects.filter(merged_into__isnull=True).order_by('name','pk')
        self.fields['project'].queryset = WorkProject.objects.all().order_by('name','pk')
        for key,label,_ in SERVICES:
            self.fields[key+'_area'] = forms.DecimalField(required=False,max_digits=8,decimal_places=2,min_value=0,label=label+' area (sq ft)')
            self.fields[key+'_rate'] = forms.DecimalField(required=False,max_digits=10,decimal_places=2,min_value=0,label=label+' rate ($/sq ft)')
        self.fields['detached_above_area'] = forms.DecimalField(required=False,max_digits=8,decimal_places=2,min_value=0,label='Detached garage upper-floor area (sq ft)')
        self.fields['hours'] = forms.DecimalField(required=False,max_digits=8,decimal_places=2,min_value=0,label='Estimated hours')
        self.fields['hourly_rate'] = forms.DecimalField(required=False,max_digits=10,decimal_places=2,min_value=0,label='Hourly rate ($)')
        for name,field in self.fields.items():
            if isinstance(field,forms.DecimalField):
                field.widget.attrs.update(step='0.01',inputmode='decimal')
                if name.endswith('_area') or name=='hours':field.widget.attrs['placeholder']='Leave blank if not used'

    def clean(self):
        data = super().clean()
        if self.errors:return data
        card = data.get('rate_version')
        if not card:
            self.add_error('rate_version','No pricing is configured for this category. Set up its rates before calculating; another category is never used automatically.')
            return data
        try:
            check_assignment(data.get('client'),data.get('project'),data.get('category'))
            for key in RATE_KEYS:
                if data.get(key+'_rate') is None:data[key+'_rate']=Decimal(card.rates[key])
            self.result = calculate(data,card)
        except ValidationError as error:
            self.add_error(None,error)
        return data


class RateCardForm(forms.Form):
    category = forms.ChoiceField(choices=CATEGORIES,widget=forms.HiddenInput)
    expected = forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    label = forms.CharField(max_length=120,label='Rate card name')
    source_note = forms.CharField(max_length=300,required=False,label='Source / reason for the update')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for key,label,_ in SERVICES:
            self.fields[key] = forms.DecimalField(max_digits=10,decimal_places=2,min_value=0,label=label+' ($/sq ft)')
        self.fields['hourly'] = forms.DecimalField(max_digits=10,decimal_places=2,min_value=0,label='Hourly rate ($)')


class ProposalFromPricingForm(forms.Form):
    version = forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    title = forms.CharField(max_length=200)
    scope = forms.CharField(max_length=10000,widget=forms.Textarea(attrs={'rows':3}))
    deliverables = forms.CharField(max_length=10000,widget=forms.Textarea(attrs={'rows':3}),help_text='Describe the drawings and other work this price includes.')
    revisions = forms.CharField(max_length=5000,required=False,widget=forms.Textarea(attrs={'rows':2}),label='Included revisions')
    exclusions = forms.CharField(max_length=5000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    timeline = forms.CharField(max_length=5000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    terms = forms.CharField(max_length=10000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    valid_until = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Valid through')
    review_note = forms.CharField(required=False,max_length=2000,widget=forms.Textarea(attrs={'rows':2}),label='Private individual-review notes',help_text='Required for multi-unit and custom-review projects. Not copied into the client proposal.')
    review_confirmed = forms.BooleanField(label='I reviewed this calculation and want to create a proposal draft.')
