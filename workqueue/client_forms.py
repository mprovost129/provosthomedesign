from django import forms
from django.core.validators import RegexValidator

from .access import authorized_projects
from .models import WorkItem


SERVICE_CHOICES = [("", "Choose a service"), ("New custom home", "New custom home"),
    ("Addition or renovation", "Addition or renovation"), ("ADU or garage", "ADU or garage"),
    ("Stock-plan modification", "Stock-plan modification"), ("Framing plans", "Framing plans"),
    ("Other / not sure", "Other / not sure")]
CATEGORIES = [(value, value) for value in ["Plans or survey", "Photos or sketches", "Answers or corrections", "Revision request", "Other"]]
NEW_FIELDS = ["billing_street", "billing_city", "billing_state", "billing_zip", "project_street",
              "project_city", "project_state", "project_zip", "service_needed", "new_description"]
UPDATE_FIELDS = ["project_reference", "project_context", "project", "update_description"]
ZIP = RegexValidator(r"^\d{5}(-\d{4})?$", "Enter a ZIP code such as 02769 or 02769-1234.")


class ClientIntakeForm(forms.Form):
    kind = forms.ChoiceField(label="Is this a new submission or an update?", choices=WorkItem.Kind.choices,
                             widget=forms.RadioSelect)
    intake_token = forms.CharField(widget=forms.HiddenInput)
    upload_ids = forms.CharField(required=False, widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput)  # Honeypot, not shown to clients.
    terms_accepted = forms.BooleanField(label="Terms accepted", required=True,
        error_messages={"required": "Please agree to the Terms & Conditions before submitting."})
    contact_full_name = forms.CharField(label="Full name", max_length=200)
    company = forms.CharField(label="Company", max_length=200,
        help_text="If you are not submitting for a company, enter Homeowner.")
    contact_email = forms.EmailField(label="Email address")
    contact_phone = forms.CharField(label="Phone number", max_length=50)
    billing_street = forms.CharField(label="Billing street address", max_length=250, required=False)
    billing_city = forms.CharField(label="Billing city", max_length=100, required=False)
    billing_state = forms.CharField(label="Billing state", max_length=50, required=False)
    billing_zip = forms.CharField(label="Billing ZIP code", max_length=20, required=False, validators=[ZIP])
    same_address = forms.BooleanField(label="Project address is the same as billing address", required=False)
    project_street = forms.CharField(label="Project street address or lot", max_length=250, required=False)
    project_city = forms.CharField(label="Project city", max_length=100, required=False)
    project_state = forms.CharField(label="Project state", max_length=50, required=False)
    project_zip = forms.CharField(label="Project ZIP code", max_length=20, required=False, validators=[ZIP])
    project_name = forms.CharField(label="Project name, if you use one", max_length=200, required=False)
    service_needed = forms.ChoiceField(label="What do you need help with?", choices=SERVICE_CHOICES, required=False)
    new_description = forms.CharField(label="Briefly describe what you would like to do.", required=False,
                                      max_length=20000, widget=forms.Textarea(attrs={"rows": 4}))
    project_reference = forms.CharField(label="Project ID, if you have it", max_length=32, required=False)
    project_context = forms.CharField(label="Project name or full address", max_length=500, required=False,
        help_text="If you do not have your ID, enter the project name or address, including town and any lot or unit number.")
    project = forms.ModelChoiceField(queryset=None, label="Choose one of your projects", required=False)
    update_description = forms.CharField(label="Please describe the files, update, or requested change.",
        max_length=20000, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    categories = forms.MultipleChoiceField(label="What are you sending?", choices=CATEGORIES, required=False,
                                          widget=forms.CheckboxSelectMultiple, help_text="Select all that apply.")
    file_link = forms.URLField(label="File or folder link, if you prefer", required=False, max_length=2000,
                               assume_scheme="https")
    requested_deadline = forms.DateField(label="Requested response or drawing date, if applicable", required=False,
                                          widget=forms.DateInput(attrs={"type": "date"}))
    approximate_size = forms.CharField(label="Approximate size or scope, if known", max_length=200, required=False)
    timeframe = forms.ChoiceField(label="What is your preferred timeframe for design work?", required=False,
        choices=[("", "Choose a timeframe (optional)"), *[(text, text) for text in
                 ["As soon as available", "1–3 months", "3–6 months", "More than 6 months", "Just exploring"]]])
    plan_number = forms.CharField(label="Plan number, if you are asking about a particular plan", max_length=100, required=False)
    preferred_contact = forms.ChoiceField(label="How would you prefer us to contact you?", required=False,
        choices=[("", "No preference"), ("Email", "Email"), ("Phone", "Phone")])
    referral_detail = forms.CharField(label="How did you hear about us?", max_length=300, required=False)
    notes = forms.CharField(label="Anything else we should know?", max_length=10000, required=False,
                            widget=forms.Textarea(attrs={"rows": 3}))
    home_preferences = forms.CharField(label="Home preferences, rooms or design goals", required=False, max_length=5000,
                                       widget=forms.Textarea(attrs={"rows": 3}))
    plan_changes = forms.CharField(label="Changes you would like made to an existing plan", required=False, max_length=5000,
                                    widget=forms.Textarea(attrs={"rows": 3}))
    framing_scope = forms.CharField(label="Framing scope or areas needing coordination", required=False, max_length=5000,
                                     widget=forms.Textarea(attrs={"rows": 3}))
    site_constraints = forms.CharField(label="Known site, HOA or permit constraints", required=False, max_length=5000,
                                       widget=forms.Textarea(attrs={"rows": 3}))
    project_contacts = forms.CharField(label="Other project contacts, if useful", required=False, max_length=3000,
                                        widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, email="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = authorized_projects(email)
        kind = self.data.get("kind", self.initial.get("kind", "new"))
        active = NEW_FIELDS if kind == "new" else UPDATE_FIELDS
        inactive = UPDATE_FIELDS if kind == "new" else NEW_FIELDS + ["project_name", "same_address", "approximate_size", "timeframe", "referral_detail", "home_preferences", "plan_changes", "framing_scope", "site_constraints", "project_contacts"]
        for name in inactive:
            self.fields[name].disabled = True
        if kind == "new":
            for name in NEW_FIELDS:
                self.fields[name].required = True
        elif kind == "update":
            self.fields["update_description"].required = True
            self.fields["categories"].required = True
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.HiddenInput, forms.RadioSelect, forms.CheckboxSelectMultiple, forms.CheckboxInput)):
                field.widget.attrs["class"] = "input"
            if name == "contact_email":
                field.widget.attrs["autocomplete"] = "email"
        if self.is_bound and self.data.get("same_address") and kind == "new":
            self.data = self.data.copy()
            for suffix in ["street", "city", "state", "zip"]:
                # Explicit client edits to copied addresses remain authoritative.
                if not self.data.get(f"project_{suffix}"):
                    self.data[f"project_{suffix}"] = self.data.get(f"billing_{suffix}", "")

    def clean(self):
        data = super().clean()
        if data.get("website"):
            raise forms.ValidationError("This submission could not be accepted.")
        if data.get("kind") == "update" and not any(data.get(name) for name in ["project", "project_reference", "project_context"]):
            self.add_error("project_context", "Choose a project, enter its ID, or provide its name or full address.")
        if data.get("file_link") and not data["file_link"].startswith("https://"):
            self.add_error("file_link", "Use a secure link beginning with https://.")
        if data.get("upload_ids") and not data.get("categories"):
            self.add_error("categories", "Choose at least one attachment category.")
        return data


class TrackingAccessForm(forms.Form):
    email = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    reference = forms.CharField(label="Project or request ID", max_length=32, required=False,
                               help_text="Use the ID from your receipt, or leave this blank to see your requests.")


class ConfirmAccessForm(forms.Form):
    token = forms.CharField(max_length=200, widget=forms.HiddenInput)
