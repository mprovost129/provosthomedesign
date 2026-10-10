from django import forms

from .models import Client, ClientContact, WorkItem, WorkProject
from .client_forms import SERVICE_CHOICES


class NewClientForm(forms.ModelForm):
    intake_token = forms.CharField(widget=forms.HiddenInput)
    full_name = forms.CharField(max_length=200, required=False, label="Contact full name")
    email = forms.EmailField(required=False, label="Email address")
    phone = forms.CharField(max_length=50, required=False, label="Phone number")

    class Meta:
        model = Client
        fields = ["name", "kind", "full_name", "email", "phone", "billing_street", "billing_city", "billing_state", "billing_zip", "internal_notes"]
        labels = {"name": "Client / company name", "kind": "Client type", "internal_notes": "Private client notes"}
        widgets = {"internal_notes": forms.Textarea(attrs={"rows": 3})}


class ContactChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.full_name or obj.client.name} — {obj.email or obj.phone or 'No email or phone recorded'}"


class ClientWorkForm(forms.ModelForm):
    field_order = ["intake_token", "upload_ids", "version", "contact", "kind", "project", "project_name", "service_needed",
                   "description", "project_street", "project_city", "project_state", "project_zip", "requested_deadline"]
    intake_token = forms.CharField(widget=forms.HiddenInput)
    upload_ids = forms.CharField(max_length=400, required=False, widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    contact = ContactChoiceField(queryset=ClientContact.objects.none(), label="Client contact")
    service_needed = forms.ChoiceField(choices=SERVICE_CHOICES, required=False, label="What do they need help with?")

    class Meta:
        model = WorkItem
        fields = ["kind", "project", "project_name", "service_needed", "description",
                  "project_street", "project_city", "project_state", "project_zip", "requested_deadline"]
        labels = {"kind": "Work type", "project": "Existing project", "service_needed": "What do they need help with?"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4}), "requested_deadline": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, client, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = client.contacts.select_related("client")
        self.fields["project"].queryset = WorkProject.objects.filter(work_items__client_contact__client=client).distinct()
        self.fields["project"].empty_label = "Create a new project"
        self.fields["project"].help_text = "Choose an existing project to add another job to it. Updates have their own place in the queue."
        self.fields["project_name"].help_text = "Needed for a new project; ignored when you choose an existing project."

    def clean(self):
        data = super().clean()
        if data.get("kind") == WorkItem.Kind.UPDATE and not data.get("project"):
            self.add_error("project", "Select the existing project for this update.")
        if not data.get("project") and not data.get("project_name"):
            self.add_error("project_name", "Enter a name for the new project.")
        return data


class ClientForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Client
        fields = ["name", "kind", "billing_street", "billing_city", "billing_state", "billing_zip", "internal_notes"]
        labels = {"name": "Client / company name", "kind": "Client type", "internal_notes": "Private client notes"}
        widgets = {"internal_notes": forms.Textarea(attrs={"rows": 4})}


class ContactForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    contact_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    email = forms.EmailField(required=False, label="Email for a new contact")
    full_name = forms.CharField(max_length=200, required=False, label="Full name")
    phone = forms.CharField(max_length=50, required=False, label="Phone number")


class ClientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        emails = ", ".join(c.email for c in obj.contacts.all() if c.email)
        return f"{obj.name} — {emails or 'No email recorded'}"


class MergeForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    target = ClientChoiceField(queryset=Client.objects.none(), label="Client record to keep")

    def __init__(self, *args, client, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Client.objects.filter(merged_into__isnull=True).exclude(pk=client.pk).prefetch_related("contacts")


class UndoForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    event_id = forms.IntegerField(widget=forms.HiddenInput)
