"""
System Settings Model for Portal Configuration
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.cache import cache


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
    facebook_url = models.URLField(blank=True, null=True)
    instagram_url = models.URLField(blank=True, null=True)
    linkedin_url = models.URLField(blank=True, null=True)
    
    # Timestamps
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'auth.User',
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
