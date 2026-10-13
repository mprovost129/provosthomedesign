from django import forms

from .models import Client, ClientContact
from .portal_models import PortalDocument
from .portal_services import google_document_url, related_clients, related_contacts


class PortalSigninForm(forms.Form):
    email = forms.EmailField(label='Your email address', widget=forms.EmailInput(attrs={'autocomplete': 'email'}))


class ContactChoice(forms.ModelChoiceField):
    def label_from_instance(self, contact):
        return f'{contact.client.name} — {contact.full_name or "Contact"} — {contact.email}'


class ClientProjectForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    client = forms.ModelChoiceField(queryset=Client.objects.filter(merged_into__isnull=True), label='Client to connect')
    confirm = forms.BooleanField(label='This client belongs to this project. This connection alone does not grant portal access.')


class MembershipForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    contact = ContactChoice(queryset=ClientContact.objects.none(), label='Client and contact')
    financial_access = forms.BooleanField(required=False, label='Include this client’s sent proposals and invoices')
    review_access = forms.BooleanField(required=False, label='Allow plan comments and revision requests')
    confirm = forms.BooleanField(label='I reviewed this email address and approve access to this project.')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contact'].queryset = related_contacts(project)


class PublicationForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    intake_token = forms.CharField(widget=forms.HiddenInput)
    upload_ids = forms.CharField(required=False, max_length=400, widget=forms.HiddenInput)
    document = forms.ModelChoiceField(queryset=PortalDocument.objects.none(), required=False,
        label='Document', empty_label='Create a new document')
    title = forms.CharField(max_length=200, required=False, label='Title for a new document')
    kind = forms.ChoiceField(choices=PortalDocument.Kind.choices, label='Category for a new document')
    billing_client = forms.ModelChoiceField(queryset=Client.objects.none(), required=False, label='Invoice belongs to')
    external_url = forms.URLField(required=False, max_length=1000, label='Or link a Google Drive file or folder')
    note = forms.CharField(max_length=1000, required=False, label='What changed in this version?', widget=forms.Textarea(attrs={'rows': 3}))
    confirm = forms.BooleanField(label='Publish this version to the approved project audience.')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document'].queryset = project.portal_documents.filter(withdrawn_at__isnull=True)
        self.fields['billing_client'].queryset = related_clients(project)

    def clean_external_url(self):
        value = self.cleaned_data['external_url']
        return google_document_url(value) if value else ''

    def clean(self):
        data = super().clean()
        if not data.get('document') and not data.get('title'):
            self.add_error('title', 'Give the new document a title.')
        if bool(data.get('external_url')) == bool(data.get('upload_ids')):
            raise forms.ValidationError('Choose one uploaded file or one Google Drive link.')
        return data
