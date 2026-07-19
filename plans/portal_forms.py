from django import forms
from django.forms import inlineformset_factory
from .models import Plans, PlanGallery, HouseStyle


class PlanForm(forms.ModelForm):
    """Form for creating and editing plans in the employee portal."""
    
    class Meta:
        model = Plans
        fields = [
            'plan_number', 'square_footage', 'bedrooms', 'bathrooms', 
            'stories', 'garage_stalls', 'house_width_in', 'house_depth_in',
            'plan_name', 'description', 'ideal_for', 'key_features',
            'layout_highlights', 'foundation_framing', 'exterior_character',
            'package_contents', 'delivery_details', 'common_modifications',
            'sku', 'meta_description', 'plan_price', 'main_image', 'house_styles',
            'is_adu', 'first_floor_primary', 'has_home_office',
            'has_walk_in_pantry', 'has_mudroom', 'has_porch_or_deck',
            'has_bonus_room', 'basement_compatible', 'narrow_lot',
            'multigenerational', 'is_available', 'is_featured'
        ]
        widgets = {
            'plan_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., PHD-2024-001'}),
            'plan_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional customer-facing name'}),
            'square_footage': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '2500'}),
            'bedrooms': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'bathrooms': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0'}),
            'stories': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'garage_stalls': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'house_width_in': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Width in inches'}),
            'house_depth_in': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Depth in inches'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'ideal_for': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'key_features': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'One feature per line'}),
            'layout_highlights': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'foundation_framing': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'exterior_character': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'package_contents': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'One item per line'}),
            'delivery_details': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'common_modifications': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'One modification per line'}),
            'sku': forms.TextInput(attrs={'class': 'form-control'}),
            'meta_description': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '180'}),
            'plan_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'main_image': forms.FileInput(attrs={'class': 'form-control'}),
            'house_styles': forms.CheckboxSelectMultiple(),
            'is_available': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_featured': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'house_width_in': 'Overall width in inches',
            'house_depth_in': 'Overall depth in inches',
            'bathrooms': 'Use 0.5 increments (e.g., 2.5)',
            'meta_description': 'SEO description (max 180 characters)',
        }


class PlanGalleryForm(forms.ModelForm):
    """Form for adding images to plan gallery."""
    
    class Meta:
        model = PlanGallery
        fields = ['image', 'kind', 'caption', 'order']
        widgets = {
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional caption'}),
            'order': forms.NumberInput(attrs={'class': 'form-control', 'value': '0'}),
        }


# Formset for managing multiple gallery images
PlanGalleryFormSet = inlineformset_factory(
    Plans,
    PlanGallery,
    form=PlanGalleryForm,
    extra=3,
    can_delete=True,
    fields=['image', 'kind', 'caption', 'order']
)


class HouseStyleForm(forms.ModelForm):
    """Form for creating house styles."""
    
    class Meta:
        model = HouseStyle
        fields = ['style_name', 'description', 'style_image', 'order']
        widgets = {
            'style_name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'style_image': forms.FileInput(attrs={'class': 'form-control'}),
            'order': forms.NumberInput(attrs={'class': 'form-control', 'value': '0'}),
        }
