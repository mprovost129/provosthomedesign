from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import User
from .models import Client, Employee, Invoice, InvoiceTemplate, InvoiceLineItem, SystemSettings


class ClientRegistrationForm(UserCreationForm):
    """Registration form for new clients."""
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={
        'class': 'form-control',
        'placeholder': 'Email address'
    }))
    first_name = forms.CharField(max_length=30, required=True, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'First name'
    }))
    last_name = forms.CharField(max_length=30, required=True, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Last name'
    }))
    company_name = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Company name (optional)'
    }))
    phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Phone number (optional)'
    }))
    
    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Username'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Password'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm password'
        })
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        
        if commit:
            user.save()
            # Create client profile
            Client.objects.create(
                user=user,
                company_name=self.cleaned_data.get('company_name', ''),
                phone=self.cleaned_data.get('phone', '')
            )
        return user


class ClientLoginForm(AuthenticationForm):
    """Login form with Bootstrap styling."""
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Username or email'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control',
        'placeholder': 'Password'
    }))


class ClientProfileForm(forms.ModelForm):
    """Form for clients to update their profile."""
    
    class Meta:
        model = Client
        fields = ('first_name', 'last_name', 'email', 'company_name', 
                  'phone_1', 'phone_1_type', 'phone_2', 'phone_2_type',
                  'address_line1', 'address_line2', 'city', 'state', 'zip_code')
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone_1': forms.TextInput(attrs={'class': 'form-control'}),
            'phone_1_type': forms.Select(attrs={'class': 'form-select'}),
            'phone_2': forms.TextInput(attrs={'class': 'form-control'}),
            'phone_2_type': forms.Select(attrs={'class': 'form-select'}),
            'address_line1': forms.TextInput(attrs={'class': 'form-control'}),
            'address_line2': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # Sync with user account if exists
        if self.user and self.instance and not self.instance.pk:
            self.fields['first_name'].initial = self.user.first_name
            self.fields['last_name'].initial = self.user.last_name
            self.fields['email'].initial = self.user.email
    
    def save(self, commit=True):
        client = super().save(commit=False)
        # Sync user account fields if user exists
        if self.user:
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            self.user.email = self.cleaned_data['email']
            if commit:
                self.user.save()
        if commit:
            client.save()
        return client


class ClientPasswordResetForm(PasswordResetForm):
    """Password reset form with Bootstrap styling."""
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control',
        'placeholder': 'Email address'
    }))


class InvoiceForm(forms.ModelForm):
    """Form for creating and editing invoices."""
    from .models import Invoice, InvoiceTemplate
    
    template = forms.ModelChoiceField(
        queryset=InvoiceTemplate.objects.filter(is_active=True),
        required=False,
        empty_label="Select a template (optional)",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'template-select'})
    )
    
    class Meta:
        model = Invoice
        fields = ['client', 'issue_date', 'due_date', 'description', 'notes', 'tax_rate']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'issue_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 
                                          'placeholder': 'Payment terms, additional notes...'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }


class InvoiceLineItemForm(forms.ModelForm):
    """Form for invoice line items."""
    from .models import InvoiceLineItem
    
    class Meta:
        model = InvoiceLineItem
        fields = ['description', 'quantity', 'unit_price', 'related_plan']
        widgets = {
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Service description'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'value': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'related_plan': forms.Select(attrs={'class': 'form-select'}),
        }


# Formset for managing multiple line items
from django.forms import inlineformset_factory

InvoiceLineItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceLineItem,
    form=InvoiceLineItemForm,
    extra=3,
    can_delete=True,
    min_num=1,
    validate_min=True
)


class ClientForm(forms.ModelForm):
    """Comprehensive form for adding/editing clients in the CRM."""
    
    class Meta:
        model = Client
        fields = [
            'first_name', 'last_name', 'company_name', 'status',
            'email', 'email_secondary', 
            'phone_1', 'phone_1_type', 'phone_2', 'phone_2_type',
            'address_line1', 'address_line2', 'city', 'state', 'zip_code', 'country',
            'website', 'tax_id', 'lead_source', 'notes'
        ]
        widgets = {
            # Basic Information
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'company_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Company or business name (optional)'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            
            # Contact Information
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'primary@email.com'}),
            'email_secondary': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'secondary@email.com (optional)'}),
            'phone_1': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 123-4567'}),
            'phone_1_type': forms.Select(attrs={'class': 'form-select'}),
            'phone_2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 987-6543 (optional)'}),
            'phone_2_type': forms.Select(attrs={'class': 'form-select'}),
            
            # Address
            'address_line1': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Street address'}),
            'address_line2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Apt, suite, unit, etc. (optional)'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'State/Province'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ZIP/Postal code'}),
            'country': forms.TextInput(attrs={'class': 'form-control'}),
            
            # Business Information
            'website': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://www.example.com'}),
            'tax_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Tax ID or EIN (optional)'}),
            
            # CRM
            'lead_source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'How did they find you?'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Internal notes about this client...'}),
        }
    
    def clean_email(self):
        """Ensure email is unique."""
        email = self.cleaned_data.get('email')
        if email:
            # Check if email already exists (excluding current instance if editing)
            qs = Client.objects.filter(email=email)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('A client with this email already exists.')
        return email


class EmployeeForm(forms.ModelForm):
    """Form for adding/editing employees."""
    
    class Meta:
        model = Employee
        fields = [
            'user', 'first_name', 'last_name', 'job_title', 'department', 'status',
            'email', 'phone_1', 'phone_1_type', 'phone_2', 'phone_2_type',
            'address_line1', 'address_line2', 'city', 'state', 'zip_code',
            'hire_date', 'emergency_contact_name', 'emergency_contact_phone',
            'can_create_invoices', 'can_manage_clients', 'can_view_reports', 'notes'
        ]
        widgets = {
            # User & Basic Information
            'user': forms.Select(attrs={'class': 'form-select'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'job_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Position/Title'}),
            'department': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Department'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            
            # Contact Information
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'work@email.com'}),
            'phone_1': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 123-4567'}),
            'phone_1_type': forms.Select(attrs={'class': 'form-select'}),
            'phone_2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 987-6543 (optional)'}),
            'phone_2_type': forms.Select(attrs={'class': 'form-select'}),
            
            # Address
            'address_line1': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Street address'}),
            'address_line2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Apt, suite, etc. (optional)'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'State'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ZIP code'}),
            
            # Employment
            'hire_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency contact name'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency phone'}),
            
            # Permissions
            'can_create_invoices': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_clients': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_view_reports': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            
            # Notes
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Internal notes about this employee...'}),
        }
    
    def clean_email(self):
        """Ensure email is unique."""
        email = self.cleaned_data.get('email')
        if email:
            qs = Employee.objects.filter(email=email)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('An employee with this email already exists.')
        return email


class SystemSettingsForm(forms.ModelForm):
    """Form for updating system settings."""
    
    class Meta:
        model = SystemSettings
        exclude = ['updated_at', 'updated_by']
        widgets = {
            # Company Information
            'company_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Your Company Name'}),
            'company_email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'contact@company.com'}),
            'company_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '(555) 555-5555'}),
            'company_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '123 Main St\nCity, State ZIP'}),
            
            # Branding
            'sidebar_primary_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'sidebar_secondary_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            
            # Portal Settings
            'portal_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Client Portal'}),
            'employee_portal_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Employee Portal'}),
            'allow_client_registration': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            
            # Invoice Settings
            'invoice_prefix': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'INV'}),
            'default_payment_terms_days': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 365}),
            'late_fee_percentage': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 100, 'step': '0.01'}),
            
            # Email Settings
            'invoice_email_subject': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Invoice #{invoice_number} from {company_name}'}),
            'invoice_reminder_days': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 365}),
            
            # Notification Settings
            'notify_on_new_client': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_on_payment': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            
            # Business Information
            'business_hours': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Monday - Friday: 9am - 5pm'}),
            'facebook_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://facebook.com/yourcompany'}),
            'instagram_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://instagram.com/yourcompany'}),
            'linkedin_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://linkedin.com/company/yourcompany'}),
        }


