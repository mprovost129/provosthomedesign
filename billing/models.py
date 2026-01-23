from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
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
    reminder_sent_count = models.PositiveIntegerField(default=0, help_text="Number of reminder emails sent")
    last_reminder_date = models.DateTimeField(null=True, blank=True)
    
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
