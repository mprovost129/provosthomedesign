from django import forms

from .models import Client


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
