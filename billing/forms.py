from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import User
from .models import (Client, Employee, Invoice, InvoiceTemplate, InvoiceLineItem, 
                     SystemSettings, Project, Proposal, ProposalLineItem, ProposalTemplate,
                     ClientPlanFile, Expense, ExpenseCategory)
import re
from datetime import datetime
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox


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
    # Temporarily disabled until reCAPTCHA is properly configured
    # captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox())
    
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
            # Create client profile with correct field names
            Client.objects.create(
                user=user,
                first_name=self.cleaned_data['first_name'],
                last_name=self.cleaned_data['last_name'],
                email=self.cleaned_data['email'],
                company_name=self.cleaned_data.get('company_name', ''),
                phone_1=self.cleaned_data.get('phone', ''),
                phone_1_type='mobile'
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
                  'address_line1', 'address_line2', 'city', 'state', 'zip_code', 'profile_picture')
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
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
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
    from .models import Invoice, InvoiceTemplate, Project
    
    template = forms.ModelChoiceField(
        queryset=InvoiceTemplate.objects.filter(is_active=True),
        required=False,
        empty_label="Select a template (optional)",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'template-select'})
    )
    
    class Meta:
        model = Invoice
        fields = ['client', 'project', 'issue_date', 'due_date', 'description', 'notes', 'tax_rate']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'issue_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 
                                          'placeholder': 'Payment terms, additional notes...'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter projects by selected client if editing
        if self.instance.pk and self.instance.client:
            self.fields['project'].queryset = Project.objects.filter(client=self.instance.client)
        else:
            self.fields['project'].queryset = Project.objects.all()
        
        # Make project field optional with better label
        self.fields['project'].required = False
        self.fields['project'].empty_label = "No project (optional)"


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
    
    send_welcome_email = forms.BooleanField(
        required=False,
        initial=False,
        label='Send Welcome Email & Create Portal Access',
        help_text='Check this box to create a portal account and email login credentials to the client',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    class Meta:
        model = Client
        fields = [
            'first_name', 'last_name', 'company_name', 'status',
            'email', 'email_secondary', 
            'phone_1', 'phone_1_type', 'phone_2', 'phone_2_type',
            'address_line1', 'address_line2', 'city', 'state', 'zip_code', 'country',
            'website', 'tax_id', 'lead_source', 'notes', 'profile_picture'
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
            
            # Profile Picture
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If editing existing client with user, show checkbox to resend
        if self.instance.pk and self.instance.user:
            self.fields['send_welcome_email'].label = 'Resend Portal Access Email'
            self.fields['send_welcome_email'].help_text = 'Check to resend portal login credentials to client'
    
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
            'can_create_invoices', 'can_manage_clients', 'can_view_reports', 'notes',
            'profile_picture'
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
            
            # Profile Picture
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'user': 'Leave blank to auto-create a portal account and send welcome email',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make user field optional - will auto-create if blank
        self.fields['user'].required = False
        self.fields['user'].empty_label = "Auto-create portal account"
    
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
            'logo_background_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'company_name_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'portal_logo': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            
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


class ProjectForm(forms.ModelForm):
    """Form for creating and editing projects."""
    
    class Meta:
        model = Project
        fields = [
            'job_number', 'job_name', 'description', 'client',
            'start_date', 'due_date', 'billing_type', 
            'fixed_price', 'hourly_rate', 'estimated_hours',
            'status', 'notes'
        ]
        widgets = {
            'job_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'YYMM## (e.g., 260123)',
                'pattern': '[0-9]{6}',
                'maxlength': '6'
            }),
            'job_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Project name or title'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Detailed project description...'
            }),
            'client': forms.Select(attrs={
                'class': 'form-select select2',
            }),
            'start_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'due_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'billing_type': forms.Select(attrs={
                'class': 'form-select',
                'id': 'id_billing_type'
            }),
            'fixed_price': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': '0.00'
            }),
            'hourly_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': '0.00'
            }),
            'estimated_hours': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.25',
                'min': '0',
                'placeholder': '0.00'
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Internal notes...'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Order clients by last name, then first name
        self.fields['client'].queryset = Client.objects.order_by('last_name', 'first_name')
    
    def clean_job_number(self):
        """Validate job number format (YYMM## where ## is the job number)"""
        job_number = self.cleaned_data.get('job_number')
        
        if not job_number:
            raise forms.ValidationError('Job number is required.')
        
        # Check format
        if not re.match(r'^\d{6}$', job_number):
            raise forms.ValidationError('Job number must be 6 digits in YYMM## format (e.g., 260123).')
        
        # Validate year and month portions
        try:
            year = int('20' + job_number[0:2])
            month = int(job_number[2:4])
            if month < 1 or month > 12:
                raise ValueError('Invalid month')
            # Job number portion (last 2 digits) can be any number 01-99
            job_num = int(job_number[4:6])
            if job_num < 1:
                raise ValueError('Job number must be at least 01')
        except ValueError as e:
            raise forms.ValidationError('Job number must have valid year/month (YYMM) followed by job number (01-99).')
        
        # Check uniqueness (excluding current instance if editing)
        qs = Project.objects.filter(job_number=job_number)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('A project with this job number already exists.')
        
        return job_number
    
    def clean(self):
        """Validate billing-specific fields"""
        cleaned_data = super().clean()
        billing_type = cleaned_data.get('billing_type')
        fixed_price = cleaned_data.get('fixed_price')
        hourly_rate = cleaned_data.get('hourly_rate')
        estimated_hours = cleaned_data.get('estimated_hours')
        
        if billing_type == 'flat_rate':
            if not fixed_price:
                self.add_error('fixed_price', 'Fixed price is required for flat rate projects.')
        
        elif billing_type == 'hourly':
            if not hourly_rate:
                self.add_error('hourly_rate', 'Hourly rate is required for hourly projects.')
            if not estimated_hours:
                self.add_error('estimated_hours', 'Estimated hours are required for hourly projects.')
        
        return cleaned_data


class ProposalForm(forms.ModelForm):
    """Form for creating and editing proposals"""
    from .models import Proposal, ProposalTemplate
    
    template = forms.ModelChoiceField(
        queryset=ProposalTemplate.objects.filter(is_active=True),
        required=False,
        empty_label="Select a template (optional)",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'template-select'}),
        help_text="Load pre-configured proposal settings"
    )
    
    class Meta:
        model = Proposal
        fields = ['client', 'project', 'title', 'description', 'issue_date', 'valid_until',
                  'tax_rate', 'deposit_percentage', 'terms_and_conditions', 'notes']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Proposal title or project name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 
                                                 'placeholder': 'Proposal introduction and overview...'}),
            'issue_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'valid_until': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'deposit_percentage': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'terms_and_conditions': forms.Textarea(attrs={'class': 'form-control', 'rows': 6,
                                                          'placeholder': 'Payment terms, conditions, warranties, etc.'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3,
                                          'placeholder': 'Internal notes (not visible to client)...'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make project optional
        self.fields['project'].required = False
        self.fields['project'].empty_label = "No project (optional)"
        
        # Filter projects by selected client if editing
        if self.instance and self.instance.client_id:
            self.fields['project'].queryset = Project.objects.filter(client=self.instance.client)
        else:
            # When creating a new proposal, don't show any projects until client is selected
            self.fields['project'].queryset = Project.objects.none()


class ProposalLineItemForm(forms.ModelForm):
    """Form for individual proposal line items"""
    
    class Meta:
        model = ProposalLineItem
        fields = ['description', 'quantity', 'rate']
        widgets = {
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2,
                                                'placeholder': 'Service or item description'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
        }


# Formset for managing multiple proposal line items
ProposalLineItemFormSet = forms.inlineformset_factory(
    Proposal,
    ProposalLineItem,
    form=ProposalLineItemForm,
    extra=5,  # Number of empty forms to display
    can_delete=True,
    min_num=1,  # Require at least one line item
    validate_min=True
)


class ProposalTemplateForm(forms.ModelForm):
    """Form for creating and editing proposal templates"""
    
    class Meta:
        model = ProposalTemplate
        fields = ['name', 'description', 'default_title', 'default_description', 'default_terms',
                  'default_valid_days', 'default_tax_rate', 'default_deposit_percentage', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Template name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2,
                                                'placeholder': 'Template description...'}),
            'default_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Default proposal title'}),
            'default_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4,
                                                        'placeholder': 'Default introduction...'}),
            'default_terms': forms.Textarea(attrs={'class': 'form-control', 'rows': 6,
                                                   'placeholder': 'Default terms and conditions...'}),
            'default_valid_days': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'default_tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'default_deposit_percentage': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ClientPlanFileForm(forms.ModelForm):
    """Form for staff to upload client plan files"""
    
    send_email_notification = forms.BooleanField(
        required=False,
        initial=True,
        label='Send email notification to client',
        help_text='Email the client to notify them about this plan file',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    class Meta:
        model = ClientPlanFile
        fields = ['client', 'project', 'file_name', 'file_type', 'version', 
              'description', 'dropbox_link', 'attachment', 'is_active']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'file_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Main Floor Plan.pdf'
            }),
            'file_type': forms.Select(attrs={'class': 'form-select'}),
            'version': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Rev 3, v2.1'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional description or notes about this file'
            }),
            'dropbox_link': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'Paste Dropbox shared link here'
            }),
            'attachment': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'dropbox_link': 'Paste the Dropbox shared link. It will be automatically formatted for download.',
        }


class ExpenseForm(forms.ModelForm):
    """Form for creating/editing expenses."""
    
    class Meta:
        model = Expense
        fields = ['description', 'amount', 'category', 'expense_date', 'vendor', 
                  'project', 'client', 'receipt_url', 'notes', 'tax_deductible', 'tax_category']
        widgets = {
            'description': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'What was purchased/paid for?'
            }),
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0.00',
                'step': '0.01',
            }),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'expense_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'vendor': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Store/vendor name (optional)'
            }),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'client': forms.Select(attrs={'class': 'form-select'}),
            'receipt_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'Dropbox or cloud storage link'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Additional details or comments'
            }),
            'tax_deductible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'tax_category': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., 6500 (Office Supplies)'
            }),
        }




