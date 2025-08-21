from __future__ import annotations

from decimal import Decimal
from typing import List
from django import forms
from django.core.files.uploadedfile import UploadedFile

from .models import Plans, HouseStyle


class MultiFileInput(forms.ClearableFileInput):
    """Enable <input type="file" multiple> with Django's ClearableFileInput."""
    allow_multiple_selected = True


# Choice helpers (store clean numeric values; show friendly labels)
SQFT_CHOICES = [(n, f"{n:,}") for n in range(1000, 6001, 100)]
BED_CHOICES = [(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5"), (6, "6+")]
BATH_CHOICES = [
    (Decimal("1.0"), "1"),
    (Decimal("1.5"), "1.5"),
    (Decimal("2.0"), "2"),
    (Decimal("2.5"), "2.5"),
    (Decimal("3.0"), "3"),
    (Decimal("3.5"), "3.5"),
    (Decimal("4.0"), "4"),
    (Decimal("4.5"), "4.5"),
    (Decimal("5.0"), "5+"),
]
STORY_CHOICES = [(1, "1"), (2, "2"), (3, "3"), (4, "4+")]
GARAGE_CHOICES = [(1, "1"), (2, "2"), (3, "3"), (4, "4+")]


class PlanQuickForm(forms.ModelForm):
    """
    Staff-only quick add form shown on the list page.
    Uses selects for most specs and supports multi-file gallery upload.
    """
    slug = forms.SlugField(required=False)

    square_footage = forms.TypedChoiceField(choices=SQFT_CHOICES, coerce=int)
    bedrooms = forms.TypedChoiceField(choices=BED_CHOICES, coerce=int)
    bathrooms = forms.TypedChoiceField(choices=BATH_CHOICES, coerce=Decimal)
    stories = forms.TypedChoiceField(choices=STORY_CHOICES, coerce=int)
    garage_stalls = forms.TypedChoiceField(choices=GARAGE_CHOICES, coerce=int)

    house_width_in = forms.IntegerField(min_value=0, label="Width (inches)")
    house_depth_in = forms.IntegerField(min_value=0, label="Depth (inches)")

    house_style = forms.ModelChoiceField(
        queryset=HouseStyle.objects.none(),  # set in __init__
        empty_label="Select style…",
    )

    is_available = forms.BooleanField(required=False, initial=True, label="Available")

    gallery_images = forms.FileField(
        widget=MultiFileInput(attrs={"multiple": True, "accept": "image/*"}),
        required=False,
        help_text="You can select multiple images.",
        label="Gallery images",
    )

    class Meta:
        model = Plans
        fields = [
            "plan_number",
            "slug",
            "house_style",
            "square_footage",
            "bedrooms",
            "bathrooms",
            "stories",
            "garage_stalls",
            "house_width_in",
            "house_depth_in",
            "description",
            "plan_price",
            "main_image",
            "is_available",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate styles in alphabetical order for the dropdown
        self.fields["house_style"].queryset = HouseStyle.objects.order_by("style_name") # type: ignore
        if not self.is_bound:
            self.fields["is_available"].initial = True

    def clean_plan_number(self) -> str:
        # Normalize plan_number (trim spaces)
        pn = (self.cleaned_data.get("plan_number") or "").strip()
        return pn

    def clean_gallery_images(self) -> List[UploadedFile]:
        # Return the list of uploaded files from the multi-file field
        return self.files.getlist("gallery_images")


class PlanCommentForm(forms.Form):
    name = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": "form-control"}))
    message = forms.CharField(required=True, widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}))

    def clean(self):
        cleaned = super().clean()
        msg = (cleaned.get("message") or "").strip()
        if not msg:
            self.add_error("message", "Please enter your message.")
        return cleaned
