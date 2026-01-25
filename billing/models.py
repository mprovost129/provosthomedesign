from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
from django.core.cache import cache
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
import uuid


class Client(models.Model):
    """Comprehensive customer database for CRM functionality."""
    
    PHONE_TYPE_CHOICES = [
        ('mobile', 'Mobile'),
        ('work', 'Work'),
        ('home', 'Home'),
        ('fax', 'Fax'),
    ]
    
    # User account (optional - clients can exist without portal access)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile', 
                                null=True, blank=True, 
                                help_text="Portal login account (optional)")
    
    # Basic Information
    first_name = models.CharField(max_length=100, blank=True, help_text="Client's first name")
    last_name = models.CharField(max_length=100, blank=True, help_text="Client's last name")
    company_name = models.CharField(max_length=200, blank=True, help_text="Company or business name")
    
    # Contact Information - Primary
    email = models.EmailField(blank=True, help_text="Primary email address")
    phone_1 = models.CharField(max_length=20, blank=True, verbose_name="Phone Number 1")
    phone_1_type = models.CharField(max_length=10, choices=PHONE_TYPE_CHOICES, default='mobile',
                                    verbose_name="Phone 1 Type")
    
    # Contact Information - Secondary
    phone_2 = models.CharField(max_length=20, blank=True, verbose_name="Phone Number 2")
    phone_2_type = models.CharField(max_length=10, choices=PHONE_TYPE_CHOICES, default='work',
                                    blank=True, verbose_name="Phone 2 Type")
    email_secondary = models.EmailField(blank=True, verbose_name="Secondary Email",
                                       help_text="Alternative email address")
    
    # Address Information
    address_line1 = models.CharField(max_length=255, blank=True, verbose_name="Address Line 1")
    address_line2 = models.CharField(max_length=255, blank=True, verbose_name="Address Line 2",
                                    help_text="Apt, suite, unit, building, floor, etc.")
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True, verbose_name="ZIP Code")
    country = models.CharField(max_length=100, blank=True, default='United States')
    
    # Business Information
    website = models.URLField(blank=True, help_text="Company website")
    tax_id = models.CharField(max_length=50, blank=True, verbose_name="Tax ID/EIN",
                             help_text="Business tax ID or EIN")
    
    # Stripe customer ID for payment processing
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    
    # CRM Fields
    lead_source = models.CharField(max_length=100, blank=True, 
                                   help_text="How did they find you? (referral, website, etc.)")
    status = models.CharField(max_length=20, default='active', choices=[
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('lead', 'Lead'),
        ('archived', 'Archived'),
    ])
    notes = models.TextField(blank=True, help_text="Internal notes about this client")
    
    # Profile Picture
    profile_picture = models.ImageField(upload_to='profile_pictures/', 
                                       null=True, blank=True,
                                       help_text="Optional profile picture")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['email']),
            models.Index(fields=['company_name']),
        ]
    
    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.company_name:
            return f"{self.company_name} ({name})" if name else self.company_name
        return name or self.email
    
    def get_display_name(self):
        """Return the best display name for the client."""
        if self.company_name:
            return self.company_name
        return f"{self.first_name} {self.last_name}".strip() or self.email
    
    def get_full_name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_full_address(self):
        """Return formatted address."""
        parts = [self.address_line1, self.address_line2, 
                 f"{self.city}, {self.state} {self.zip_code}".strip()]
        return '\n'.join([p for p in parts if p])


class Employee(models.Model):
    """Employee database separate from clients for staff management."""
    
    PHONE_TYPE_CHOICES = [
        ('mobile', 'Mobile'),
        ('work', 'Work'),
        ('home', 'Home'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('on_leave', 'On Leave'),
    ]
    
    # User account for portal access
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile',
                                help_text="Portal login account")
    
    # Basic Information
    first_name = models.CharField(max_length=100, help_text="Employee's first name")
    last_name = models.CharField(max_length=100, help_text="Employee's last name")
    job_title = models.CharField(max_length=100, blank=True, help_text="Job title or position")
    department = models.CharField(max_length=100, blank=True, help_text="Department")
    
    # Contact Information
    email = models.EmailField(help_text="Work email address")
    phone_1 = models.CharField(max_length=20, blank=True, verbose_name="Primary Phone")
    phone_1_type = models.CharField(max_length=10, choices=PHONE_TYPE_CHOICES, default='mobile',
                                    verbose_name="Phone Type")
    phone_2 = models.CharField(max_length=20, blank=True, verbose_name="Secondary Phone")
    phone_2_type = models.CharField(max_length=10, choices=PHONE_TYPE_CHOICES, default='work',
                                    blank=True, verbose_name="Phone 2 Type")
    
    # Address Information
    address_line1 = models.CharField(max_length=255, blank=True, verbose_name="Address Line 1")
    address_line2 = models.CharField(max_length=255, blank=True, verbose_name="Address Line 2")
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True, verbose_name="ZIP Code")
    
    # Employment Information
    hire_date = models.DateField(null=True, blank=True, help_text="Date of hire")
    status = models.CharField(max_length=20, default='active', choices=STATUS_CHOICES)
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    
    # Permissions & Access
    can_create_invoices = models.BooleanField(default=False, 
                                             help_text="Can create and manage invoices")
    can_manage_clients = models.BooleanField(default=False,
                                            help_text="Can add/edit client information")
    can_view_reports = models.BooleanField(default=False,
                                          help_text="Can access financial reports")
    
    # Internal Notes
    notes = models.TextField(blank=True, help_text="Internal notes about this employee")
    
    # Profile Picture
    profile_picture = models.ImageField(upload_to='profile_pictures/', 
                                       null=True, blank=True,
                                       help_text="Optional profile picture")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.job_title:
            return f"{name} ({self.job_title})"
        return name
    
    def get_full_name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_display_name(self):
        """Return display name with title."""
        name = self.get_full_name()
        if self.job_title:
            return f"{name} - {self.job_title}"
        return name


class Invoice(models.Model):
    """Client invoices for architectural services."""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    ]
    
    # Unique invoice number (auto-generated)
    invoice_number = models.CharField(max_length=50, unique=True, editable=False)
    
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='invoices')
    project = models.ForeignKey('Project', on_delete=models.SET_NULL, null=True, blank=True, 
                                related_name='invoices', 
                                help_text="Optional: Link to project")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Dates
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField()
    paid_date = models.DateField(null=True, blank=True)
    
    # Description/notes
    description = models.TextField(blank=True, help_text="Overall invoice description")
    notes = models.TextField(blank=True, help_text="Additional notes or payment terms")
    
    # Totals (calculated from line items)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), 
                                   help_text="Tax percentage (e.g., 6.25 for 6.25%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    # Payment tracking
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    # Public payment link (no login required)  
    payment_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    
    # Email tracking
    email_sent_date = models.DateTimeField(null=True, blank=True, help_text="When invoice was last emailed")
    email_sent_count = models.PositiveIntegerField(default=0, help_text="Number of times invoice was sent")
    viewed_date = models.DateTimeField(null=True, blank=True, help_text="When client first viewed invoice")
    reminder_sent_count = models.PositiveIntegerField(default=0, help_text="Number of reminder emails sent")
    last_reminder_date = models.DateTimeField(null=True, blank=True)
    
    # Overdue reminder tracking
    reminder_sent = models.BooleanField(default=False, help_text="Whether overdue reminder was sent")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-issue_date', '-invoice_number']
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['due_date']),
        ]
    
    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.client} - ${self.total}"
    
    def save(self, *args, **kwargs):
        # Generate invoice number if not exists
        if not self.invoice_number:
            # Format: INV-YYYYMMDD-XXXX
            from django.utils import timezone
            date_part = timezone.now().strftime('%Y%m%d')
            # Get last invoice of the day
            last_invoice = Invoice.objects.filter(
                invoice_number__startswith=f'INV-{date_part}'
            ).order_by('invoice_number').last()
            
            if last_invoice:
                last_num = int(last_invoice.invoice_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.invoice_number = f'INV-{date_part}-{new_num:04d}'
        
        super().save(*args, **kwargs)
    
    def calculate_totals(self):
        """Recalculate invoice totals from line items."""
        self.subtotal = sum(item.total for item in self.line_items.all())
        self.tax_amount = (self.subtotal * self.tax_rate / Decimal('100')).quantize(Decimal('0.01'))
        self.total = self.subtotal + self.tax_amount
        self.save()
    
    def get_balance_due(self):
        """Return outstanding balance."""
        return self.total - self.amount_paid
    
    def is_paid(self):
        """Check if invoice is fully paid."""
        return self.amount_paid >= self.total
    
    def is_overdue(self):
        """Check if invoice is past due date."""
        if self.status == 'paid':
            return False
        return timezone.now().date() > self.due_date
    
    def get_absolute_url(self):
        return reverse('billing:invoice_detail', kwargs={'pk': self.pk})
    
    def get_public_payment_url(self):
        """Get the public payment URL using token (no login required)."""
        return reverse('billing:public_payment', kwargs={'token': self.payment_token})
    
    def mark_as_sent(self):
        """Mark invoice as sent and update tracking."""
        self.status = 'sent'
        self.email_sent_date = timezone.now()
        self.email_sent_count += 1
        self.save()
    
    def mark_as_paid(self, amount=None, payment_date=None):
        """Mark invoice as paid."""
        if amount is None:
            amount = self.get_balance_due()
        self.amount_paid += amount
        if self.is_paid():
            self.status = 'paid'
            self.paid_date = payment_date or timezone.now().date()
        self.save()


class InvoiceLineItem(models.Model):
    """Individual line items on an invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    
    # Optional: link to a plan if this is for a specific house plan
    related_plan = models.ForeignKey('plans.Plans', on_delete=models.SET_NULL, 
                                     null=True, blank=True, related_name='invoice_items')
    
    order = models.PositiveIntegerField(default=0, help_text="Display order")
    
    class Meta:
        ordering = ['order', 'id']
    
    def __str__(self):
        return f"{self.description} - ${self.total}"
    
    def save(self, *args, **kwargs):
        # Calculate total
        self.total = (self.quantity * self.unit_price).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
        
        # Update invoice totals
        if self.invoice_id:
            self.invoice.calculate_totals()


class Payment(models.Model):
    """Payment records for invoices."""
    
    PAYMENT_METHOD_CHOICES = [
        ('stripe_card', 'Credit/Debit Card'),
        ('stripe_ach', 'Bank Transfer (ACH)'),
        ('check', 'Check'),
        ('cash', 'Cash'),
        ('wire', 'Wire Transfer'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
    
    # Unique payment ID
    payment_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Stripe-specific fields
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_charge_id = models.CharField(max_length=255, blank=True, null=True)
    
    # Check/manual payment details
    reference_number = models.CharField(max_length=100, blank=True, 
                                       help_text="Check number, confirmation number, etc.")
    
    notes = models.TextField(blank=True)
    
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['invoice', 'status']),
        ]
    
    def __str__(self):
        return f"Payment {self.payment_id} - ${self.amount} - {self.get_status_display()}"
    
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        # Update invoice amount_paid if payment succeeded
        if self.status == 'succeeded':
            if not self.processed_at:
                self.processed_at = timezone.now()
                super().save(update_fields=['processed_at'])
            
            # Recalculate invoice amount_paid
            total_paid = self.invoice.payments.filter(status='succeeded').aggregate(
                total=models.Sum('amount')
            )['total'] or Decimal('0.00')
            
            self.invoice.amount_paid = total_paid
            
            # Update invoice status if fully paid
            if self.invoice.amount_paid >= self.invoice.total:
                self.invoice.status = 'paid'
                if not self.invoice.paid_date:
                    self.invoice.paid_date = timezone.now().date()
            
            self.invoice.save()


class InvoiceTemplate(models.Model):
    """Predefined invoice templates for quick invoice creation."""
    name = models.CharField(max_length=100, unique=True, 
                           help_text="Template name (e.g., 'Custom Home Design')")
    description = models.TextField(help_text="What this template is for")
    
    # Default values for invoices created from this template
    default_description = models.TextField(blank=True, 
                                          help_text="Default invoice description")
    default_notes = models.TextField(blank=True, 
                                    help_text="Default payment terms/notes")
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, 
                                          default=Decimal('0.00'),
                                          help_text="Default tax rate (e.g., 6.25 for 6.25%)")
    days_until_due = models.PositiveIntegerField(default=30, 
                                                 help_text="Default payment terms in days")
    
    # Store line items as JSON
    # Format: [{"description": "...", "quantity": 1, "unit_price": 100.00}, ...]
    default_line_items = models.JSONField(
        default=list,
        blank=True,
        help_text="Default line items for this template"
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def create_invoice_from_template(self, client):
        """Create a new invoice from this template."""
        from datetime import timedelta
        
        invoice = Invoice.objects.create(
            client=client,
            description=self.default_description,
            notes=self.default_notes,
            tax_rate=self.default_tax_rate,
            due_date=timezone.now().date() + timedelta(days=self.days_until_due)
        )
        
        # Create line items
        for item_data in self.default_line_items:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description=item_data.get('description', ''),
                quantity=Decimal(str(item_data.get('quantity', 1))),
                unit_price=Decimal(str(item_data.get('unit_price', 0)))
            )
        
        # Calculate totals
        invoice.calculate_totals()
        return invoice


class SystemSettings(models.Model):
    """
    Single-row settings table for portal configuration.
    Uses singleton pattern - only one row should exist.
    """
    
    # Company Information
    company_name = models.CharField(max_length=200, default="Provost Home Design")
    company_email = models.EmailField(default="mike@provosthomedesign.com")
    company_phone = models.CharField(max_length=20, default="508-243-7312")
    company_address = models.TextField(default="7 Park St. Unit 1, Rehoboth, MA 02769")
    
    # Branding - Sidebar Colors
    sidebar_primary_color = models.CharField(
        max_length=7, 
        default="#004080",
        help_text="Hex color for sidebar (e.g., #004080)"
    )
    sidebar_secondary_color = models.CharField(
        max_length=7,
        default="#002850", 
        help_text="Hex color for sidebar gradient (e.g., #002850)"
    )
    
    # Branding - Logo Area
    logo_background_color = models.CharField(
        max_length=7,
        default="#FFFFFF",
        help_text="Background color for logo area (e.g., #FFFFFF for white)"
    )
    company_name_color = models.CharField(
        max_length=7,
        default="#004080",
        help_text="Color for company name text below logo (e.g., #004080)"
    )
    
    # Branding - Logo
    portal_logo = models.ImageField(
        upload_to='brand/',
        null=True,
        blank=True,
        help_text="Portal logo (displayed in sidebar and login pages). Falls back to phdlogo.svg if not set."
    )
    
    # Portal Settings
    portal_title = models.CharField(max_length=100, default="Client Portal")
    employee_portal_title = models.CharField(max_length=100, default="Employee Portal")
    allow_client_registration = models.BooleanField(
        default=False,
        help_text="Allow clients to self-register"
    )
    
    # Invoice Settings
    invoice_prefix = models.CharField(
        max_length=10,
        default="INV",
        help_text="Prefix for invoice numbers (e.g., INV-2024-001)"
    )
    default_payment_terms_days = models.IntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Default payment terms in days"
    )
    late_fee_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Late fee percentage (0-100)"
    )
    
    # Email Settings
    invoice_email_subject = models.CharField(
        max_length=200,
        default="New Invoice from {company_name}",
        help_text="Use {company_name}, {invoice_number} as placeholders"
    )
    invoice_reminder_days = models.IntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Days before due date to send reminder"
    )
    
    # Notification Settings
    notify_on_new_client = models.BooleanField(
        default=True,
        help_text="Email notification when new client registers"
    )
    notify_on_payment = models.BooleanField(
        default=True,
        help_text="Email notification when payment received"
    )
    
    # Business Hours
    business_hours = models.TextField(
        default="Monday-Friday: 9:00 AM - 5:00 PM",
        help_text="Display business hours on portal"
    )
    
    # Social Media Links
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    
    # Timestamps
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='settings_updates'
    )
    
    class Meta:
        verbose_name = "System Settings"
        verbose_name_plural = "System Settings"
    
    def save(self, *args, **kwargs):
        # Ensure only one settings row exists
        self.pk = 1
        super().save(*args, **kwargs)
        # Clear cache when settings change
        cache.delete('system_settings')
    
    def delete(self, *args, **kwargs):
        # Prevent deletion
        pass
    
    @classmethod
    def load(cls):
        """Load settings from database or create default"""
        settings = cache.get('system_settings')
        if settings is None:
            settings, created = cls.objects.get_or_create(pk=1)
            cache.set('system_settings', settings, 60 * 60)  # Cache for 1 hour
        return settings
    
    def __str__(self):
        return f"System Settings - {self.company_name}"


class Project(models.Model):
    """Project management with job tracking and billing."""
    
    STATUS_CHOICES = [
        ('quoted', 'Quoted'),
        ('approved', 'Approved'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('on_hold', 'On Hold'),
        ('cancelled', 'Cancelled'),
    ]
    
    BILLING_TYPE_CHOICES = [
        ('flat_rate', 'Flat Rate'),
        ('hourly', 'Hourly Rate'),
    ]
    
    # Job identification
    job_number = models.CharField(
        max_length=10, 
        unique=True, 
        help_text="Job number in YYMM## format (e.g., 260123 = Year 26, Month 01, Job #23)"
    )
    job_name = models.CharField(max_length=200, help_text="Project name or title")
    description = models.TextField(blank=True, help_text="Detailed project description")
    
    # Client relationship
    client = models.ForeignKey(
        Client, 
        on_delete=models.PROTECT, 
        related_name='projects',
        help_text="Client for this project"
    )
    
    # Project timeline
    start_date = models.DateField(null=True, blank=True, help_text="Project start date")
    due_date = models.DateField(null=True, blank=True, help_text="Project due date")
    completed_date = models.DateField(null=True, blank=True, help_text="Actual completion date")
    
    # Billing information
    billing_type = models.CharField(
        max_length=20, 
        choices=BILLING_TYPE_CHOICES, 
        default='flat_rate',
        help_text="How this project is billed"
    )
    fixed_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Fixed price for flat rate projects"
    )
    hourly_rate = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Hourly rate for hourly projects"
    )
    estimated_hours = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Estimated hours for completion"
    )
    actual_hours = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Actual hours worked"
    )
    
    # Project status
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='quoted',
        help_text="Current project status"
    )
    
    # Project closure
    is_closed = models.BooleanField(default=False, help_text="Whether project is closed")
    closed_date = models.DateTimeField(null=True, blank=True, help_text="When project was closed")
    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_projects',
        help_text="Staff member who closed this project"
    )
    
    # Notes and metadata
    notes = models.TextField(blank=True, help_text="Internal notes about the project")
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='projects_created',
        help_text="Staff member who created this project"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-job_number']  # Most recent jobs first
        indexes = [
            models.Index(fields=['job_number']),
            models.Index(fields=['client', 'status']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.job_number} - {self.job_name}"
    
    def get_absolute_url(self):
        return reverse('billing:project_detail', kwargs={'pk': self.pk})
    
    @property
    def estimated_total(self):
        """Calculate estimated project total based on billing type"""
        if self.billing_type == 'flat_rate' and self.fixed_price:
            return self.fixed_price
        elif self.billing_type == 'hourly' and self.hourly_rate and self.estimated_hours:
            return self.hourly_rate * self.estimated_hours
        return Decimal('0.00')
    
    @property
    def actual_total(self):
        """Calculate actual project total based on billing type"""
        if self.billing_type == 'flat_rate' and self.fixed_price:
            return self.fixed_price
        elif self.billing_type == 'hourly' and self.hourly_rate:
            return self.hourly_rate * self.actual_hours
        return Decimal('0.00')
    
    @property
    def is_overbudget(self):
        """Check if project is over estimated hours/budget"""
        if self.billing_type == 'hourly' and self.estimated_hours:
            return self.actual_hours > self.estimated_hours
        return False
    
    @property
    def hours_remaining(self):
        """Calculate remaining hours for hourly projects"""
        if self.estimated_hours:
            return max(Decimal('0.00'), self.estimated_hours - self.actual_hours)
        return None
    
    @property
    def progress_percentage(self):
        """Calculate project progress based on hours"""
        if self.estimated_hours and self.estimated_hours > 0:
            progress = (self.actual_hours / self.estimated_hours) * 100
            return min(100, round(progress, 1))
        return 0
    
    def get_total_invoiced(self):
        """Get total amount invoiced for this project."""
        return sum(inv.total for inv in self.invoices.exclude(status='cancelled'))
    
    def get_total_paid(self):
        """Get total amount paid for this project."""
        return sum(inv.amount_paid for inv in self.invoices.exclude(status='cancelled'))
    
    def get_balance_due(self):
        """Get remaining balance due for this project."""
        return self.get_total_invoiced() - self.get_total_paid()
    
    def is_fully_paid(self):
        """Check if all invoices are paid in full."""
        invoices = self.invoices.exclude(status='cancelled')
        if not invoices.exists():
            return False
        return all(inv.is_paid() for inv in invoices)
    
    def get_payment_status(self):
        """Return payment status: 'unpaid', 'partial', or 'paid'."""
        total_invoiced = self.get_total_invoiced()
        total_paid = self.get_total_paid()
        
        if total_paid == 0:
            return 'unpaid'
        elif total_paid >= total_invoiced:
            return 'paid'
        else:
            return 'partial'
    
    def get_payment_summary(self):
        """Get payment summary dict with all details."""
        total_invoiced = self.get_total_invoiced()
        total_paid = self.get_total_paid()
        balance = total_invoiced - total_paid
        
        return {
            'total_invoiced': total_invoiced,
            'total_paid': total_paid,
            'balance_due': balance,
            'percentage_paid': (total_paid / total_invoiced * 100) if total_invoiced > 0 else 0,
            'status': self.get_payment_status(),
            'is_fully_paid': self.is_fully_paid(),
        }


class Proposal(models.Model):
    """Project proposals for clients with line items and acceptance tracking."""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('viewed', 'Viewed'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]
    
    # Identification
    proposal_number = models.CharField(max_length=50, unique=True, help_text="Format: PROP-YYMM##")
    title = models.CharField(max_length=255, help_text="Proposal title or project name")
    
    # Relationships
    client = models.ForeignKey('Client', on_delete=models.PROTECT, related_name='proposals',
                              help_text="Client receiving this proposal")
    project = models.ForeignKey('Project', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='proposals',
                               help_text="Project created when proposal is accepted")
    # Backlink to the invoice created from this proposal (if any)
    linked_invoice = models.OneToOneField('Invoice', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='linked_proposal',
                                         help_text="Invoice generated from this proposal")
    
    # Status & Dates
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    issue_date = models.DateField(default=timezone.now, help_text="Date proposal was created")
    valid_until = models.DateField(help_text="Proposal expiration date")
    sent_date = models.DateTimeField(null=True, blank=True, help_text="When proposal was sent to client")
    viewed_date = models.DateTimeField(null=True, blank=True, help_text="When client first viewed proposal")
    accepted_date = models.DateTimeField(null=True, blank=True, help_text="When client accepted proposal")
    rejected_date = models.DateTimeField(null=True, blank=True, help_text="When client rejected proposal")
    
    # Content
    description = models.TextField(blank=True, help_text="Proposal introduction and overview")
    terms_and_conditions = models.TextField(blank=True, help_text="Terms, conditions, and payment details")
    
    # Financial
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)],
                                   help_text="Tax rate as percentage (e.g., 7.5 for 7.5%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    # Deposit/Retainer
    deposit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'),
                                            validators=[MinValueValidator(0), MaxValueValidator(100)],
                                            help_text="Deposit as percentage of total (e.g., 50 for 50%)")
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                        help_text="Required deposit/retainer amount")
    
    # Acceptance
    accepted_by = models.CharField(max_length=200, blank=True, help_text="Name of person who accepted")
    acceptance_signature = models.TextField(blank=True, help_text="Digital signature data (optional)")
    acceptance_ip = models.GenericIPAddressField(null=True, blank=True, help_text="IP address of acceptance")
    
    # Internal
    notes = models.TextField(blank=True, help_text="Internal notes (not visible to client)")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='proposals_created')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-issue_date', '-created_at']
        indexes = [
            models.Index(fields=['proposal_number']),
            models.Index(fields=['client', 'status']),
            models.Index(fields=['status', 'valid_until']),
        ]
    
    def __str__(self):
        return f"{self.proposal_number} - {self.title}"
    
    def save(self, *args, **kwargs):
        """Auto-generate proposal number if not set"""
        if not self.proposal_number:
            self.proposal_number = self.generate_proposal_number()
        
        # Calculate tax and total
        self.tax_amount = (self.subtotal * self.tax_rate) / Decimal('100')
        self.total = self.subtotal + self.tax_amount
        
        # Calculate deposit amount from percentage if set
        if self.deposit_percentage > 0:
            self.deposit_amount = (self.total * self.deposit_percentage) / Decimal('100')
        
        super().save(*args, **kwargs)
    
    @classmethod
    def generate_proposal_number(cls):
        """Generate next proposal number in format PROP-YYMM##"""
        from datetime import datetime
        
        now = datetime.now()
        date_part = now.strftime('%y%m')  # YYMM (e.g., "2601" for January 2026)
        prefix = f'PROP-{date_part}'
        
        # Find last proposal with this prefix
        last_proposal = cls.objects.filter(
            proposal_number__startswith=prefix
        ).order_by('-proposal_number').first()
        
        if last_proposal:
            # Extract only the last 2 digits after PROP-YYMM
            # Format is PROP-YYMM##, so we want characters after position 9
            try:
                number_part = last_proposal.proposal_number[9:]  # Get everything after "PROP-YYMM"
                last_number = int(number_part)
                next_number = last_number + 1
            except (ValueError, IndexError):
                # If parsing fails, start from 1
                next_number = 1
        else:
            next_number = 1
        
        return f"{prefix}{next_number:02d}"
    
    def get_absolute_url(self):
        return reverse('billing:proposal_detail', kwargs={'pk': self.pk})
    
    @property
    def is_expired(self):
        """Check if proposal has expired"""
        if self.status in ['accepted', 'rejected']:
            return False
        return timezone.now().date() > self.valid_until
    
    @property
    def days_until_expiration(self):
        """Calculate days until expiration"""
        if self.is_expired:
            return 0
        delta = self.valid_until - timezone.now().date()
        return delta.days
    
    def calculate_totals(self):
        """Recalculate subtotal from line items"""
        self.subtotal = sum(item.amount for item in self.line_items.all())
        self.tax_amount = (self.subtotal * self.tax_rate) / Decimal('100')
        self.total = self.subtotal + self.tax_amount
        if self.deposit_percentage > 0:
            self.deposit_amount = (self.total * self.deposit_percentage) / Decimal('100')
        self.save()


class ProposalLineItem(models.Model):
    """Individual line items for proposals"""
    
    proposal = models.ForeignKey('Proposal', on_delete=models.CASCADE, related_name='line_items')
    description = models.TextField(help_text="Service or item description")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'),
                                   validators=[MinValueValidator(0)])
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                              validators=[MinValueValidator(0)],
                              help_text="Price per unit")
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    order = models.PositiveIntegerField(default=0, help_text="Display order")
    
    class Meta:
        ordering = ['order', 'id']
    
    def __str__(self):
        return f"{self.proposal.proposal_number} - {self.description[:50]}"
    
    def save(self, *args, **kwargs):
        """Calculate amount from quantity * rate"""
        self.amount = self.quantity * self.rate
        super().save(*args, **kwargs)


class ProposalTemplate(models.Model):
    """Reusable proposal templates with predefined content"""
    
    name = models.CharField(max_length=200, unique=True, help_text="Template name")
    description = models.TextField(blank=True, help_text="Template description")
    
    # Default Content
    default_title = models.CharField(max_length=255, blank=True, help_text="Default proposal title")
    default_description = models.TextField(blank=True, help_text="Default proposal introduction")
    default_terms = models.TextField(blank=True, help_text="Default terms and conditions")
    
    # Default Settings
    default_valid_days = models.PositiveIntegerField(default=30, help_text="Days until proposal expires")
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'),
                                          validators=[MinValueValidator(0), MaxValueValidator(100)])
    default_deposit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'),
                                                     validators=[MinValueValidator(0), MaxValueValidator(100)])
    
    # Line Items (stored as JSON for flexibility)
    line_items_json = models.JSONField(default=list, blank=True,
                                       help_text="Default line items as JSON array")
    
    # Status
    is_active = models.BooleanField(default=True, help_text="Active templates appear in dropdown")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Activity(models.Model):
    """Track all interactions and activities with clients and projects."""
    
    ACTIVITY_TYPES = [
        ('note', 'Note'),
        ('call', 'Phone Call'),
        ('email', 'Email'),
        ('meeting', 'Meeting'),
        ('site_visit', 'Site Visit'),
        ('proposal_sent', 'Proposal Sent'),
        ('proposal_viewed', 'Proposal Viewed'),
        ('proposal_accepted', 'Proposal Accepted'),
        ('proposal_rejected', 'Proposal Rejected'),
        ('invoice_sent', 'Invoice Sent'),
        ('invoice_viewed', 'Invoice Viewed'),
        ('payment_received', 'Payment Received'),
        ('project_started', 'Project Started'),
        ('project_completed', 'Project Completed'),
        ('status_change', 'Status Change'),
        ('other', 'Other'),
    ]
    
    # Relationships
    client = models.ForeignKey('Client', on_delete=models.CASCADE, related_name='activities',
                              help_text="Associated client")
    project = models.ForeignKey('Project', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='activities',
                               help_text="Associated project (optional)")
    proposal = models.ForeignKey('Proposal', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='activities',
                                 help_text="Associated proposal (optional)")
    invoice = models.ForeignKey('Invoice', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='activities',
                               help_text="Associated invoice (optional)")
    
    # Activity Details
    activity_type = models.CharField(max_length=30, choices=ACTIVITY_TYPES, default='note')
    title = models.CharField(max_length=200, help_text="Brief activity title")
    description = models.TextField(blank=True, help_text="Detailed notes or description")
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='activities_created',
                                  help_text="Staff member who logged this activity")
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Optional fields
    is_pinned = models.BooleanField(default=False, help_text="Pin important activities to top")
    is_internal = models.BooleanField(default=False, 
                                     help_text="Internal note (not visible to client)")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Activities'
        indexes = [
            models.Index(fields=['client', '-created_at']),
            models.Index(fields=['project', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.client} - {self.created_at.strftime('%Y-%m-%d')}"


class ClientPlanFile(models.Model):
    """
    Dropbox-linked plan files for clients.
    Stores Dropbox shared links instead of uploading files to avoid storage costs.
    """
    
    FILE_TYPE_CHOICES = [
        ('floor_plan', 'Floor Plan'),
        ('elevation', 'Elevation'),
        ('site_plan', 'Site Plan'),
        ('structural', 'Structural Drawing'),
        ('electrical', 'Electrical Plan'),
        ('plumbing', 'Plumbing Plan'),
        ('rendering', '3D Rendering'),
        ('other', 'Other'),
    ]
    
    # Relationships
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='plan_files',
        help_text="Client who can access this file"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='plan_files',
        null=True,
        blank=True,
        help_text="Associated project (optional)"
    )
    
    # File information
    file_name = models.CharField(
        max_length=255,
        help_text="Display name for the file (e.g., 'Main Floor Plan.pdf')"
    )
    file_type = models.CharField(
        max_length=20,
        choices=FILE_TYPE_CHOICES,
        default='other',
        help_text="Type of plan file"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description or notes about this file"
    )
    
    # Dropbox link
    dropbox_link = models.URLField(
        max_length=500,
        help_text="Dropbox shared link (use direct download link: ?dl=1)"
    )
    
    # Metadata
    version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Version number or revision (e.g., 'Rev 3', 'v2.1')"
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='plan_files_uploaded',
        help_text="Staff member who added this file"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # Visibility
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide from client (useful for outdated versions)"
    )
    
    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Client Plan File'
        verbose_name_plural = 'Client Plan Files'
        indexes = [
            models.Index(fields=['client', 'is_active', '-uploaded_at']),
            models.Index(fields=['project', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.file_name} - {self.client}"
    
    def get_direct_download_link(self):
        """Convert Dropbox link to direct download format"""
        if '?dl=0' in self.dropbox_link:
            return self.dropbox_link.replace('?dl=0', '?dl=1')
        elif '?dl=' not in self.dropbox_link:
            return f"{self.dropbox_link}?dl=1"
        return self.dropbox_link
    
    def get_preview_link(self):
        """Get Dropbox preview link (for viewing in browser)"""
        if '?dl=1' in self.dropbox_link:
            return self.dropbox_link.replace('?dl=1', '?dl=0')
        elif '?dl=0' not in self.dropbox_link:
            return f"{self.dropbox_link}?dl=0"
        return self.dropbox_link


# ==================== EXPENSES ====================

class ExpenseCategory(models.Model):
    """Categories for organizing business expenses (office, travel, meals, etc.)."""
    
    name = models.CharField(max_length=100, unique=True, help_text="e.g., Office Supplies, Travel, Meals")
    description = models.TextField(blank=True, help_text="Details about this expense category")
    is_tax_deductible = models.BooleanField(default=True, help_text="Is this category tax deductible?")
    is_active = models.BooleanField(default=True, help_text="Whether this category is available for new expenses")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Expense Categories'
    
    def __str__(self):
        return self.name


class Expense(models.Model):
    """Track business expenses, optionally linked to projects/clients."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('reimbursed', 'Reimbursed'),
    ]
    
    # Basic Info
    description = models.CharField(max_length=255, help_text="What was purchased/paid for?")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))],
                                help_text="Total expense amount")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    
    # Dates
    expense_date = models.DateField(help_text="Date the expense occurred")
    submitted_date = models.DateTimeField(auto_now_add=True)
    reimbursed_date = models.DateField(null=True, blank=True, help_text="Date reimbursed (if applicable)")
    
    # Relationships (optional)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='expenses', help_text="Associated project (optional)")
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='expenses', help_text="Associated client (optional)")
    
    # Details
    vendor = models.CharField(max_length=200, blank=True, help_text="Where was this purchased from?")
    receipt_url = models.URLField(blank=True, help_text="Link to receipt (Dropbox or cloud storage)")
    notes = models.TextField(blank=True, help_text="Additional notes or details")
    
    # Tax & Tracking
    tax_deductible = models.BooleanField(default=True, help_text="Is this expense tax deductible?")
    tax_category = models.CharField(max_length=100, blank=True, help_text="Tax category for accounting (e.g., 6500 - Office Supplies)")
    
    # Status & Approval
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending',
                             help_text="Approval status of this expense")
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='approved_expenses', help_text="User who approved this")
    approved_date = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                  related_name='submitted_expenses', help_text="User who submitted this")
    
    class Meta:
        ordering = ['-expense_date']
        indexes = [
            models.Index(fields=['-expense_date']),
            models.Index(fields=['status']),
            models.Index(fields=['project']),
            models.Index(fields=['client']),
        ]
    
    def __str__(self):
        return f"{self.description} - ${self.amount} ({self.expense_date})"
    
    def get_status_display_color(self):
        """Return Bootstrap color for status badge."""
        colors = {
            'pending': 'warning',
            'approved': 'success',
            'rejected': 'danger',
            'reimbursed': 'info',
        }
        return colors.get(self.status, 'secondary')
    
    def approve(self, user):
        """Approve this expense."""
        self.status = 'approved'
        self.approved_by = user
        self.approved_date = timezone.now()
        self.save()
    
    def reject(self):
        """Reject this expense."""
        self.status = 'rejected'
        self.save()
    
    def mark_reimbursed(self, date=None):
        """Mark expense as reimbursed."""
        self.status = 'reimbursed'
        self.reimbursed_date = date or timezone.now().date()
        self.save()

