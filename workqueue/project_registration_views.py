from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from . import project_registration as registration
from .dropbox_filing import refresh_index
from .dropbox_models import DropboxFolder
from .dropbox_provider import DropboxError
from .freshbooks import BillingError
from .forms import StyledForm
from .models import ClientContact, WorkProject
from .pm_views import staff_view, editable, error_text


class ContactChoice(forms.ModelChoiceField):
    def label_from_instance(self, contact):
        return f'{contact.client.name} — {contact.full_name or contact.email or "Contact"} ({contact.email or "no email"})'


class RegistrationForm(StyledForm, forms.ModelForm):
    contact = ContactChoice(label='CRM client and contact', queryset=ClientContact.objects.none(),
        help_text='Select the correct client/company. Its FreshBooks billing connection must already exist.')
    folder = forms.ModelChoiceField(label='Existing Dropbox project folder', queryset=DropboxFolder.objects.none(),
        help_text='Refresh the folder list if needed. The remote folder is verified again before registration.')
    remote_id = forms.IntegerField(label='FreshBooks project ID', min_value=1, max_value=999999999999999999,
        help_text='Use the actual FreshBooks project ID, not an invoice ID, mirror record ID, or business project number.')

    class Meta:
        model = WorkProject
        fields = ['contact', 'folder', 'remote_id', 'name', 'street', 'city', 'state', 'zip_code']
        labels = {'name': 'PHD project name', 'street': 'Project street address', 'zip_code': 'ZIP / postal code'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contact'].queryset = ClientContact.objects.filter(
            archived_at__isnull=True, client__merged_into__isnull=True).select_related('client').order_by('client__name', 'full_name')
        self.fields['folder'].queryset = DropboxFolder.objects.filter(available=True)
        self.style_fields()


@staff_view
@require_http_methods(['GET', 'POST'])
def register_existing(request):
    editable(request)
    registration.require_registration_staff(request.user)
    form = RegistrationForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'preview' else None)
    snapshot = signed = existing = None
    status = 200
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'refresh':
                refresh_index()
                messages.success(request, 'Existing Dropbox folders refreshed. No project was registered.')
                return redirect('workqueue:register_existing_project')
            if action == 'register':
                project, created = registration.register(signed=request.POST.get('signed', ''), actor=request.user)
                messages.success(request, 'Existing project registered. No work was added to the queue.' if created else 'This project is already registered. No duplicate was created.')
                return redirect(reverse('workqueue:project_detail', args=[project.pk]))
            if action != 'preview':
                raise ValidationError('Choose Verify and review before registering.')
            if form.is_valid():
                data = {**form.cleaned_data, 'contact': str(form.cleaned_data['contact'].pk), 'folder': form.cleaned_data['folder'].pk}
                snapshot, signed, existing = registration.preview(data=data, actor=request.user)
            else:
                status = 400
        except (ValidationError, DropboxError, BillingError) as exc:
            if form.is_bound:
                form.add_error(None, error_text(exc))
            else:
                messages.error(request, error_text(exc))
            status = 400
    return render(request, 'workqueue/project_registration.html',
        {'form': form, 'review': snapshot, 'signed': signed, 'existing': existing}, status=status)
