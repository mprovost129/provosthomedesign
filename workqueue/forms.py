from django import forms

from .models import Priority, Status, WorkItem, WorkProject


class StyledForm:
    def style_fields(self):
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


class MilestoneEmailChoice(forms.Form):
    client_email = forms.ChoiceField(label="Client milestone email", required=False, initial="skip",
        choices=[("skip", "Skip email"), ("send", "Send when work starts or finishes")],
        help_text="Only a change to In Progress or Completed sends an email. Internal notes stay private.")


class QueueEditForm(StyledForm, MilestoneEmailChoice, forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = WorkItem
        fields = ["status", "priority", "queue_order", "project", "estimated_days", "requested_deadline",
                  "committed_due_date", "scheduled_start", "estimated_completion", "followup_date", "internal_notes",
                  "project_name", "project_street", "project_city", "project_state", "project_zip",
                  "service_needed", "description"]
        widgets = {name: forms.DateInput(attrs={"type": "date"}) for name in
                   ["requested_deadline", "committed_due_date", "scheduled_start", "estimated_completion", "followup_date"]}
        widgets["internal_notes"] = forms.Textarea(attrs={"rows": 3})
        widgets["description"] = forms.Textarea(attrs={"rows": 3})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = self.instance.version
        self.fields["project"].queryset = WorkProject.objects.all()
        self.fields["project"].label = "Connected project"
        self.fields["queue_order"].help_text = "Lower numbers appear earlier. Equal values use received time and request ID."
        self.fields['project_name'].help_text = 'Changing the name or address also updates the connected project for its linked requests.'
        if self.instance.project_id:
            for field, attribute in [('project_name', 'name'), ('project_street', 'street'),
                                     ('project_city', 'city'), ('project_state', 'state'), ('project_zip', 'zip_code')]:
                self.initial[field] = getattr(self.instance.project, attribute)
        # Older open queue forms lack the newly editable intake fields.
        if self.is_bound:
            self.data = self.data.copy()
            for name in ['project_name', 'project_street', 'project_city', 'project_state',
                         'project_zip', 'service_needed', 'description']:
                if self.add_prefix(name) not in self.data:
                    self.data[self.add_prefix(name)] = self.initial.get(name, '')
        self.fields['description'].required = False
        self.style_fields()


class QuickStatusForm(MilestoneEmailChoice):
    version = forms.IntegerField(min_value=1)
    status = forms.ChoiceField(choices=Status.choices)


class InformationRequestForm(StyledForm, forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    token = forms.UUIDField(widget=forms.HiddenInput)
    message = forms.CharField(label="Message to your client", max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}), help_text="Only this message goes to the client. Internal notes are never included.")
    followup_date = forms.DateField(label="Remind me to follow up (optional)", required=False,
        widget=forms.DateInput(attrs={"type": "date"}), help_text="Appears under Follow-ups due. This date is internal and is not emailed.")
    reminder_date = forms.DateField(label="Send the client one reminder on (optional)", required=False,
        widget=forms.DateInput(attrs={"type": "date"}), help_text="Leave blank to skip. Stops after a verified response, a newer information request, or a status change.")

    def clean_reminder_date(self):
        from django.utils import timezone
        value = self.cleaned_data.get('reminder_date')
        if value and value <= timezone.localdate():
            raise forms.ValidationError('Choose a future date for the client reminder.')
        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


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


class CopyWorkForm(StyledForm, forms.ModelForm):
    source_id = forms.UUIDField(widget=forms.HiddenInput)
    source_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    submission_token = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = WorkItem
        fields = ['kind', 'project', 'project_context', 'project_name', 'project_street',
                  'project_city', 'project_state', 'project_zip', 'service_needed', 'description',
                  'requested_deadline']
        widgets = {'description': forms.Textarea(attrs={'rows': 3}),
                   'requested_deadline': forms.DateInput(attrs={'type': 'date'})}
        labels = {'project': 'Connected project (optional)', 'description': 'Requested work'}
        help_texts = {'project': 'Leave blank for a separate new project, or select the original project for linked work.',
                      'project_context': 'For an unconnected update, enter the project ID, name or address.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def clean(self):
        data = super().clean()
        if data.get('kind') == WorkItem.Kind.UPDATE and not (data.get('project') or data.get('project_context')):
            self.add_error('project_context', 'Select a project or enter its ID, name or address.')
        if data.get('kind') == WorkItem.Kind.NEW and not (data.get('project_name') or data.get('project_street') or data.get('project')):
            self.add_error('project_name', 'Enter a project name or address.')
        return data


class QueueDeleteForm(forms.Form):
    version = forms.IntegerField(min_value=1)
    confirmed = forms.BooleanField(required=True)
