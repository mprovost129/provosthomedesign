from __future__ import annotations
from django import forms
from django.core.validators import RegexValidator
from django.forms.widgets import ClearableFileInput
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox

# dynamic styles from admin
from plans.models import HouseStyle

# ---- Phone validation ----
PHONE_RE = r"^[0-9\-\+\(\)\.\s]{7,}$"
phone_validator = RegexValidator(PHONE_RE, "Enter a valid phone number.")

# ---- Multi-file input widget (enables multiple uploads safely) ----
class MultiFileInput(ClearableFileInput):
    allow_multiple_selected = True

# ---- File constraints for uploaded plans ----
ALLOWED_PLAN_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
MAX_PLAN_FILE_MB = 10        # per-file limit
MAX_PLAN_TOTAL_MB = 20       # combined files limit

_zip_validator = RegexValidator(
    regex=r"^\d{5}(-\d{4})?$",
    message="Enter a valid ZIP code (12345 or 12345-6789).",
)

# ---- Square footage choices: 1,000 → 6,000 by 100 ----
SQFT_CHOICES = [(str(n), f"{n:,} sq ft") for n in range(1000, 6001, 100)]


class NewHouseForm(forms.Form):
    # -------- Contact --------
    first_name = forms.CharField(max_length=50, required=True)
    last_name = forms.CharField(max_length=50, required=True)
    email = forms.EmailField(required=True)
    alt_email = forms.EmailField(required=False)
    company = forms.CharField(max_length=50, required=False)
    phone_number = forms.CharField(max_length=20, required=True, validators=[phone_validator])
    alt_phone_number = forms.CharField(max_length=20, required=False, validators=[phone_validator])

    preferred_contact_method = forms.ChoiceField(
        choices=[("email", "Email"), ("phone", "Phone"), ("text", "Text")],
        required=True,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # -------- Current address --------
    street_address = forms.CharField(max_length=50, required=False)
    city = forms.CharField(max_length=50, required=False)
    state = forms.CharField(max_length=50, required=False)
    zip_code = forms.CharField(max_length=10, required=False, validators=[_zip_validator])

    # -------- Project preferences --------
    # Dynamic from admin (plans.HouseStyle)
    house_style = forms.ModelChoiceField(
        queryset=HouseStyle.objects.none(),   # set in __init__
        required=False,
        empty_label=None,                     # custom empty label in __init__
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select House Style"}),
        label="House style",
    )

    # ChoiceFields for min/max sq ft (string values)
    min_square_footage = forms.ChoiceField(
        choices=SQFT_CHOICES,
        required=False,
        label="Min sq ft",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Min Sq Ft"}),
    )
    max_square_footage = forms.ChoiceField(
        choices=SQFT_CHOICES,
        required=False,
        label="Max sq ft",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Max Sq Ft"}),
    )

    budget = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"placeholder": "Enter Budget"}),
    )

    number_of_floors = forms.ChoiceField(
        choices=[("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")],
        required=False,
        label="Floors",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Floors"}),
    )
    number_of_bedrooms = forms.ChoiceField(
        choices=[("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"), ("6", "6")],
        required=False,
        label="Bedrooms",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Bedrooms"}),
    )
    number_of_bathrooms = forms.ChoiceField(
        choices=[("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5")],
        required=False,
        label="Bathrooms",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Bathrooms"}),
    )
    number_of_garage_spaces = forms.ChoiceField(
        choices=[("0", "0"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")],
        required=False,
        label="Garage spaces",
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Garage Spaces"}),
    )

    # -------- Land --------
    land_purchased = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    land_address = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"placeholder": "Enter Property Address"}))
    land_city = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"placeholder": "Enter Property City"}))
    land_state = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"placeholder": "Enter Property State"}))
    land_zip_code = forms.CharField(
        max_length=10,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Enter Property Zip Code"}),
        validators=[_zip_validator],
    )
    land_size = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"placeholder": "Enter Property Size"}))

    # -------- Plans / structure --------
    pre_existing_plans = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))

    plan_files = forms.FileField(
        required=False,
        widget=MultiFileInput(
            attrs={
                "multiple": True,
                "accept": ".pdf,.png,.jpg,.jpeg,.webp",
                "class": "form-control",
            }
        ),
        label="Upload pre-existing plan(s)",
        help_text="PDF/PNG/JPG/WEBP. Max 10MB each, 20MB total.",
    )

    foundation_height = forms.ChoiceField(
        choices=[("crawl_space", "Crawl Space"), ("8_ft", "8 ft"), ("9_ft", "9 ft"), ("10_ft", "10 ft"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Foundation Height"}),
    )
    first_floor_height = forms.ChoiceField(
        choices=[("8_ft", "8 ft"), ("9_ft", "9 ft"), ("10_ft", "10 ft"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select First Floor Ceiling Height"}),
    )
    second_floor_height = forms.ChoiceField(
        choices=[("8_ft", "8 ft"), ("9_ft", "9 ft"), ("10_ft", "10 ft"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Second Floor Ceiling Height (If Applicable)"}),
    )
    third_floor_height = forms.ChoiceField(
        choices=[("8_ft", "8 ft"), ("9_ft", "9 ft"), ("10_ft", "10 ft"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Third Floor Ceiling Height (If Applicable)"}),
    )
    ceiling_feature_1 = forms.ChoiceField(
        choices=[("vaulted", "Vaulted"), ("tray", "Tray"), ("stepped", "Stepped"), ("coffered", "Coffered"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Ceiling Feature 1"}),
    )
    ceiling_feature_2 = forms.ChoiceField(
        choices=[("vaulted", "Vaulted"), ("tray", "Tray"), ("stepped", "Stepped"), ("coffered", "Coffered"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Ceiling Feature 2"}),
    )
    ceiling_feature_3 = forms.ChoiceField(
        choices=[("vaulted", "Vaulted"), ("tray", "Tray"), ("stepped", "Stepped"), ("coffered", "Coffered"), ("other", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "placeholder": "Select Ceiling Feature 3"}),
    )

    additional_notes = forms.CharField(
        widget=forms.Textarea(attrs={"placeholder": "Enter Additional Notes", "rows": 4}),
        required=False,
    )

    # Required Terms & Conditions consent
    terms_accepted = forms.BooleanField(
        required=True,
        label="I agree to the Terms & Conditions",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    # Honeypot
    website = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # populate dynamic house styles (ordered as in admin)
        self.fields["house_style"].queryset = HouseStyle.objects.order_by("order", "style_name") # type: ignore

        # Add Bootstrap classes to inputs (don’t clobber selects/checkboxes)
        for _, field in self.fields.items():
            existing = field.widget.attrs.get("class", "")
            classes = set(filter(None, existing.split()))
            if "form-check-input" not in classes and "form-select" not in classes:
                classes.add("form-control")
            field.widget.attrs["class"] = " ".join(sorted(classes))

        # Ensure a blank option for optional selects
        optional_selects = [
            "house_style",
            "foundation_height",
            "first_floor_height",
            "second_floor_height",
            "third_floor_height",
            "ceiling_feature_1",
            "ceiling_feature_2",
            "ceiling_feature_3",
            "number_of_floors",
            "number_of_bedrooms",
            "number_of_bathrooms",
            "number_of_garage_spaces",
            "min_square_footage",
            "max_square_footage",
        ]
        for fname in optional_selects:
            field = self.fields[fname]
            placeholder = field.widget.attrs.get("placeholder", "Select an option")
            # ModelChoiceField: set empty_label; ChoiceField: insert ("", placeholder)
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = placeholder
            elif not field.required:
                choices = list(field.choices)
                if not choices or choices[0][0] != "":
                    choices.insert(0, ("", placeholder))
                    field.choices = choices

    # -------- Validation --------
    def clean(self):
        cleaned = super().clean()

        # min/max sq ft sanity (ChoiceField values are strings)
        mi_raw = cleaned.get("min_square_footage")
        ma_raw = cleaned.get("max_square_footage")

        def _to_int(v):
            try:
                return int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        mi = _to_int(mi_raw)
        ma = _to_int(ma_raw)
        if mi is not None and ma is not None and mi > ma:
            self.add_error("max_square_footage", "Max square footage must be greater than or equal to min.")

        # validate uploaded plan files (multi-file)
        files = self.files.getlist("plan_files")
        if files:
            total_bytes = 0
            for f in files:
                size = getattr(f, "size", 0) or 0
                total_bytes += size

                if size > MAX_PLAN_FILE_MB * 1024 * 1024:
                    self.add_error("plan_files", f"'{f.name}' is larger than {MAX_PLAN_FILE_MB}MB.")

                ctype = (getattr(f, "content_type", "") or "").lower()
                name_lc = f.name.lower()
                ok_type = (
                    ctype in ALLOWED_PLAN_CONTENT_TYPES
                    or name_lc.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp"))
                )
                if not ok_type:
                    self.add_error("plan_files", f"'{f.name}' must be a PDF or image (PNG/JPG/WEBP).")

            if total_bytes > MAX_PLAN_TOTAL_MB * 1024 * 1024:
                self.add_error("plan_files", f"Total attachment size exceeds {MAX_PLAN_TOTAL_MB}MB.")

        # Honeypot
        if cleaned.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return cleaned


class ContactForm(forms.Form):
    # Honeypot (bots only)
    website = forms.CharField(required=False, widget=forms.HiddenInput())

    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=30, required=False, validators=[phone_validator])
    subject = forms.CharField(max_length=120, required=False)
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))
    terms_accepted = forms.BooleanField(
        required=True,
        label="I agree to the Terms & Conditions",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to inputs (don’t override checkboxes/hidden/selects)
        for _, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput, forms.Select)):
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            self.add_error(None, "Invalid submission.")
        if not cleaned.get("terms_accepted"):
            self.add_error("terms_accepted", "You must agree to the Terms & Conditions.")
        return cleaned


class TestimonialForm(forms.Form):
    # Honeypot (bots only)
    website2 = forms.CharField(required=False, widget=forms.HiddenInput())

    name = forms.CharField(max_length=100, label="Name")
    email = forms.EmailField(required=False, label="Email (not shown)")

    role = forms.ChoiceField(
        choices=[
            ("", "Select Role"),   # placeholder
            ("homeowner", "Homeowner"),
            ("builder", "Builder"),
            ("architect", "Architect"),
            ("engineer", "Engineer"),
            ("designer", "Designer"),
            ("other", "Other"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="I am a...",
    )

    rating = forms.ChoiceField(
        choices=[("5", "5"), ("4", "4"), ("3", "3"), ("2", "2"), ("1", "1")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), label="Your experience")

    consent_to_publish = forms.BooleanField(
        required=False,
        label="I consent to publish this testimonial",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    terms_accepted = forms.BooleanField(
        required=True,
        label="I agree to the Terms & Conditions",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for _, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput, forms.Select)):
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website2"):
            self.add_error(None, "Invalid submission.")
        if not cleaned.get("terms_accepted"):
            self.add_error("terms_accepted", "You must agree to the Terms & Conditions.")
        return cleaned
