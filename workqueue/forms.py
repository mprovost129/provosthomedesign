from django import forms

from .models import Priority, Status, WorkItem, WorkProject


class StyledForm:
    def style_fields(self):
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


class QueueEditForm(StyledForm, forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = WorkItem
        fields = ["status", "priority", "queue_order", "project", "estimated_days", "requested_deadline",
                  "committed_due_date", "scheduled_start", "estimated_completion", "followup_date", "internal_notes"]
        widgets = {name: forms.DateInput(attrs={"type": "date"}) for name in
                   ["requested_deadline", "committed_due_date", "scheduled_start", "estimated_completion", "followup_date"]}
        widgets["internal_notes"] = forms.Textarea(attrs={"rows": 3})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = self.instance.version
        self.fields["project"].queryset = WorkProject.objects.all()
        self.fields["project"].label = "Connected project"
        self.fields["queue_order"].help_text = "Lower numbers appear earlier. Equal values use received time and request ID."
        self.style_fields()


class QuickStatusForm(forms.Form):
    version = forms.IntegerField(min_value=1)
    status = forms.ChoiceField(choices=Status.choices)


class ManualWorkForm(StyledForm, forms.ModelForm):
    submission_token = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = WorkItem
        fields = ["kind", "project", "project_context", "contact_full_name", "company", "contact_email",
                  "contact_phone", "project_name", "service_needed", "description",
                  "billing_street", "billing_city", "billing_state", "billing_zip",
                  "project_street", "project_city", "project_state", "project_zip", "requested_deadline"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "requested_deadline": forms.DateInput(attrs={"type": "date"})}
        labels = {"contact_full_name": "Full name", "contact_email": "Email address",
                  "contact_phone": "Phone number", "project": "Connected project"}
        help_texts = {"company": "Enter Homeowner if there is no company.",
                      "project_context": "Project ID, name or address, if an update is not connected yet."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def clean(self):
        data = super().clean()
        if data.get("kind") == WorkItem.Kind.UPDATE and not (data.get("project") or data.get("project_context")):
            self.add_error("project_context", "Select a project or enter its ID, name or address.")
        return data
