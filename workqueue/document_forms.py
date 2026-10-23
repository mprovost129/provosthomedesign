from django import forms
from .pm_forms import ProposalForm, draft_data


class InvoiceDocumentForm(ProposalForm):
    document_kind = forms.ChoiceField(choices=[('invoice', 'Invoice draft')], initial='invoice', label='Document type')
    issue_date = forms.DateField(label='Date prepared', widget=forms.DateInput(attrs={'type': 'date'}))
    due_date = forms.DateField(label='Proposed due date', widget=forms.DateInput(attrs={'type': 'date'}))
    document_reference = forms.CharField(label='Client / project reference', max_length=100, required=False)
    notes = forms.CharField(label='Client notes', max_length=10000, required=False, widget=forms.Textarea(attrs={'rows': 3}))
    valid_until = forms.DateField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('scope', 'deliverables', 'revisions', 'exclusions', 'timeline'):
            self.fields[name].required = False
            self.fields[name].widget = forms.HiddenInput()
        self.fields['pricing'].widget = forms.HiddenInput()
        self.fields['pricing'].initial = 'fixed'

    def clean(self):
        data = super().clean()
        if data.get('due_date') and data.get('issue_date') and data['due_date'] < data['issue_date']:
            self.add_error('due_date', 'The proposed due date cannot be before the date prepared.')
        data['valid_until'] = data.get('due_date')
        return data


def invoice_form_data(form, lines):
    data = draft_data(form, lines)
    data.update(document_kind='invoice', document_style='phd-studio-v1', pricing='fixed',
        issue_date=form.cleaned_data['issue_date'].isoformat(), due_date=form.cleaned_data['due_date'].isoformat(),
        document_reference=form.cleaned_data.get('document_reference', ''), notes=form.cleaned_data.get('notes', ''))
    return data
