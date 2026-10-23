from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.forms import BaseFormSet, formset_factory
from .models import Client, ClientContact, WorkItem
from .pm_models import ProjectPlan, ProjectTask, ProposalTemplate


class PlanForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    class Meta:
        model = ProjectPlan
        fields = ['scope', 'target_date', 'estimated_hours', 'internal_notes']
        widgets = {'scope': forms.Textarea(attrs={'rows': 4}), 'internal_notes': forms.Textarea(attrs={'rows': 4}),
                   'target_date': forms.DateInput(attrs={'type': 'date'})}
        labels = {'scope': 'Working scope', 'target_date': 'Project target date', 'estimated_hours': 'Estimated hours', 'internal_notes': 'Private project notes'}
    def clean_estimated_hours(self):
        value = self.cleaned_data['estimated_hours']
        if value is not None and value < 0:
            raise forms.ValidationError('Hours cannot be negative.')
        return value


class TaskForm(forms.ModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput)
    class Meta:
        model = ProjectTask
        fields = ['title', 'due_date', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2}), 'due_date': forms.DateInput(attrs={'type': 'date'})}


class TemplateForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    class Meta:
        model = ProposalTemplate
        fields = ['name', 'scope', 'deliverables', 'revisions', 'exclusions', 'terms', 'archived']
        labels = {'revisions': 'Included revisions', 'terms': 'Payment and other terms', 'archived': 'Hide from new proposals'}
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in ['scope', 'deliverables', 'revisions', 'exclusions', 'terms']}


class ProposalForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    client = forms.ModelChoiceField(queryset=Client.objects.none(), label='Client to bill')
    title = forms.CharField(max_length=200)
    document_kind = forms.ChoiceField(choices=[('estimate', 'Estimate'), ('proposal', 'Proposal')], required=False, initial='proposal', label='Document type')
    pricing = forms.ChoiceField(choices=[('fixed', 'Fixed price'), ('hourly', 'Hourly estimate')], label='Pricing method')
    scope = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={'rows': 4}))
    deliverables = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={'rows': 4}))
    revisions = forms.CharField(required=False, max_length=5000, label='Included revisions', widget=forms.Textarea(attrs={'rows': 3}))
    exclusions = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={'rows': 3}))
    timeline = forms.CharField(required=False, max_length=5000, label='Expected timing', widget=forms.Textarea(attrs={'rows': 2}))
    terms = forms.CharField(required=False, max_length=10000, label='Payment and other terms', widget=forms.Textarea(attrs={'rows': 4}))
    valid_until = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), label='Valid through')
    issue_date = forms.DateField(required=False, label='Date prepared', widget=forms.DateInput(attrs={'type': 'date'}))
    document_reference = forms.CharField(required=False, max_length=100, label='Client / project reference')
    notes = forms.CharField(required=False, max_length=10000, label='Client notes', widget=forms.Textarea(attrs={'rows': 3}))
    def __init__(self, *args, project, proposal=None, **kwargs):
        super().__init__(*args, **kwargs)
        ids = list(WorkItem.objects.filter(project=project, client_contact__isnull=False).values_list('client_contact__client_id', flat=True))
        ids += list(project.client_links.values_list('client_id', flat=True))
        if proposal:
            from .pm_services import current_client
            ids = list(ids) + [current_client(proposal.client).pk]
        self.fields['client'].queryset = Client.objects.filter(pk__in=ids, merged_into__isnull=True)
        kind = self.data.get('document_kind') or self.initial.get('document_kind', 'proposal')
        if kind == 'estimate':
            self.fields['scope'].required = self.fields['deliverables'].required = False
        for name in ('scope', 'deliverables', 'revisions', 'exclusions', 'timeline', 'notes', 'terms'):
            self.fields[name + '_html'] = forms.CharField(required=False, max_length=40000, widget=forms.HiddenInput,
                initial=(self.initial.get('rich_sections') or {}).get(name, ''))

    def clean_document_kind(self):
        return self.cleaned_data['document_kind'] or self.initial.get('document_kind', 'proposal')


class LineForm(forms.Form):
    description = forms.CharField(max_length=1000, label='Service / description', widget=forms.Textarea(attrs={'rows': 2}))
    quantity = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal('0.01'), label='Quantity / hours')
    rate = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0'), label='Rate ($)')

class OrderedLines(BaseFormSet):
    ordering_widget = forms.HiddenInput


LineFormSet = formset_factory(LineForm, formset=OrderedLines, extra=0, can_order=True, can_delete=True, min_num=1, validate_min=True,
                              max_num=20, validate_max=True, absolute_max=25)

def draft_data(form, lines):
    data = {name: form.cleaned_data[name] for name in ['title', 'pricing', 'scope', 'deliverables', 'revisions', 'exclusions', 'timeline', 'terms']}
    data['valid_until'] = form.cleaned_data['valid_until'].isoformat()
    data.update(document_kind=form.cleaned_data.get('document_kind') or 'proposal', document_style='phd-studio-v1',
        issue_date=form.cleaned_data['issue_date'].isoformat() if form.cleaned_data.get('issue_date') else '',
        document_reference=form.cleaned_data.get('document_reference', ''), notes=form.cleaned_data.get('notes', ''))
    data['rich_sections'] = {name: form.cleaned_data.get(name + '_html', '') for name in ('scope','deliverables','revisions','exclusions','timeline','notes','terms')}
    from .document_content import checked_rich
    data['rich_sections'] = checked_rich(data)
    data['lines'] = []
    total = Decimal('0')
    for line_form in lines.ordered_forms:
        line = line_form.cleaned_data
        if not line or line.get('DELETE'):
            continue
        amount = (line['quantity'] * line['rate']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        total += amount
        data['lines'].append({'description': line['description'], 'quantity': str(line['quantity']), 'rate': str(line['rate']), 'amount': str(amount)})
    if total > Decimal('999999999.99'):
        raise forms.ValidationError('The total is too large. Review quantities and rates.')
    data['total'] = str(total.quantize(Decimal('.01')))
    return data


class SendForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    contact = forms.ModelChoiceField(queryset=ClientContact.objects.none(), label='Send to')
    confirm = forms.BooleanField(label='I reviewed the proposal and want to email this version to the selected contact.')
    def __init__(self, *args, client, document_kind='proposal', **kwargs):
        super().__init__(*args, **kwargs)
        if document_kind == 'estimate':
            self.fields['confirm'].label = 'I reviewed the estimate and want to email this version to the selected contact.'
        self.fields['contact'].queryset = client.contacts.filter(archived_at__isnull=True).exclude(email__isnull=True).exclude(email='')
        self.fields['contact'].label_from_instance = lambda obj: f'{obj.full_name or client.name} — {obj.email}'


class ResponseForm(forms.Form):
    action = forms.ChoiceField(choices=[('accept', 'Accept proposal'), ('changes', 'Request changes')], widget=forms.HiddenInput)
    full_name = forms.CharField(max_length=200, label='Your full name')
    agreement = forms.BooleanField(required=False, label='I have reviewed and accept this proposal’s scope, pricing, and terms.')
    note = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={'rows': 4}), label='Message')
    def __init__(self, *args, document_kind='proposal', **kwargs):
        super().__init__(*args, **kwargs)
        if document_kind == 'estimate':
            self.fields['agreement'].label = 'I have reviewed and accept this estimate’s pricing and terms.'
    def clean(self):
        data = super().clean()
        if data.get('action') == 'accept' and not data.get('agreement'):
            self.add_error('agreement', 'Confirm acceptance before submitting.')
        if data.get('action') == 'changes' and not data.get('note'):
            self.add_error('note', 'Describe the changes you would like.')
        return data
