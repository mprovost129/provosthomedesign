from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from . import models
from .models import Client, Employee, Invoice, InvoiceLineItem, Payment, InvoiceTemplate


class InvoiceLineItemInline(admin.TabularInline):
    """Inline editing of invoice line items."""
    model = InvoiceLineItem
    extra = 1
    fields = ('description', 'quantity', 'unit_price', 'total', 'related_plan', 'order')
    readonly_fields = ('total',)
    ordering = ['order', 'id']


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """Comprehensive CRM-style admin interface for Client management."""
    list_display = ('display_name', 'company_name', 'email', 'phone_1_display', 
                    'city', 'state', 'status', 'total_invoices', 'total_outstanding', 'created_at')
    list_filter = ('status', 'state', 'phone_1_type', 'lead_source', 'created_at')
    search_fields = ('first_name', 'last_name', 'email', 'email_secondary', 'company_name', 
                     'phone_1', 'phone_2', 'user__username')
    list_per_page = 50
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('first_name', 'last_name', 'company_name', 'status')
        }),
        ('Primary Contact', {
            'fields': (('email', 'email_secondary'),
                      ('phone_1', 'phone_1_type'),
                      ('phone_2', 'phone_2_type'))
        }),
        ('Address', {
            'fields': ('address_line1', 'address_line2', 
                      ('city', 'state', 'zip_code'), 'country')
        }),
        ('Business Information', {
            'fields': ('website', 'tax_id'),
            'classes': ('collapse',)
        }),
        ('CRM Information', {
            'fields': ('lead_source', 'notes')
        }),
        ('Portal Access', {
            'fields': ('user',),
            'classes': ('collapse',),
            'description': 'Optional: Link to a user account for portal login'
        }),
        ('Payment Integration', {
            'fields': ('stripe_customer_id',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')
    
    def display_name(self, obj):
        """Display client name with link."""
        name = obj.get_full_name()
        if obj.user:
            return format_html('{} <span style="color: #27ae60;">●</span>', name)
        return name
    display_name.short_description = 'Client Name'
    display_name.admin_order_field = 'last_name'
    
    def phone_1_display(self, obj):
        """Display primary phone with type."""
        if obj.phone_1:
            return f"{obj.phone_1} ({obj.get_phone_1_type_display()})"
        return '-'
    phone_1_display.short_description = 'Primary Phone'
    
    def total_invoices(self, obj):
        count = obj.invoices.count()
        if count:
            url = reverse('admin:billing_invoice_changelist') + f'?client__id__exact={obj.id}'
            return format_html('<a href="{}">{} invoice{}</a>', url, count, 's' if count != 1 else '')
        return '0'
    total_invoices.short_description = 'Invoices'
    
    def total_outstanding(self, obj):
        from decimal import Decimal
        outstanding = Decimal('0.00')
        for inv in obj.invoices.exclude(status='paid'):
            outstanding += inv.get_balance_due()
        if outstanding > 0:
            return format_html('<span style="color: #d63031; font-weight: bold;">${:,.2f}</span>', outstanding)
        return format_html('<span style="color: #27ae60;">$0.00</span>')
    total_outstanding.short_description = 'Outstanding'


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    """Admin interface for Employee management."""
    list_display = ('display_name', 'job_title', 'department', 'email', 'phone_1_display',
                    'status', 'hire_date', 'permissions_display', 'created_at')
    list_filter = ('status', 'department', 'can_create_invoices', 'can_manage_clients', 
                   'can_view_reports', 'hire_date')
    search_fields = ('first_name', 'last_name', 'email', 'phone_1', 'phone_2', 
                     'user__username', 'job_title', 'department')
    list_per_page = 50
    date_hierarchy = 'hire_date'
    
    fieldsets = (
        ('User Account', {
            'fields': ('user',),
            'description': 'Link to user account for portal login'
        }),
        ('Basic Information', {
            'fields': ('first_name', 'last_name', 'job_title', 'department', 'status')
        }),
        ('Contact Information', {
            'fields': (('email',),
                      ('phone_1', 'phone_1_type'),
                      ('phone_2', 'phone_2_type'))
        }),
        ('Address', {
            'fields': ('address_line1', 'address_line2', 
                      ('city', 'state', 'zip_code')),
            'classes': ('collapse',)
        }),
        ('Employment Details', {
            'fields': ('hire_date', ('emergency_contact_name', 'emergency_contact_phone'))
        }),
        ('Portal Permissions', {
            'fields': ('can_create_invoices', 'can_manage_clients', 'can_view_reports'),
            'description': 'Additional permissions beyond standard staff access'
        }),
        ('Internal Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')
    
    def display_name(self, obj):
        """Display employee name with active indicator."""
        name = obj.get_full_name()
        if obj.status == 'active':
            return format_html('{} <span style="color: #27ae60;">●</span>', name)
        elif obj.status == 'on_leave':
            return format_html('{} <span style="color: #f39c12;">●</span>', name)
        else:
            return format_html('{} <span style="color: #95a5a6;">●</span>', name)
    display_name.short_description = 'Employee Name'
    display_name.admin_order_field = 'last_name'
    
    def phone_1_display(self, obj):
        """Display primary phone with type."""
        if obj.phone_1:
            return f"{obj.phone_1} ({obj.get_phone_1_type_display()})"
        return '-'
    phone_1_display.short_description = 'Primary Phone'
    
    def permissions_display(self, obj):
        """Display active permissions as badges."""
        perms = []
        if obj.can_create_invoices:
            perms.append('Invoices')
        if obj.can_manage_clients:
            perms.append('Clients')
        if obj.can_view_reports:
            perms.append('Reports')
        if obj.user.is_superuser:
            return format_html('<span style="color: #d63031; font-weight: bold;">SUPERUSER</span>')
        elif obj.user.is_staff:
            return format_html('<span style="color: #0984e3;">Staff{}</span>', 
                             f' + {", ".join(perms)}' if perms else '')
        return ', '.join(perms) if perms else '-'
    permissions_display.short_description = 'Permissions'


class PaymentInline(admin.TabularInline):
    """Inline display of payments."""
    model = Payment
    extra = 0
    fields = ('amount', 'payment_method', 'status', 'reference_number', 'processed_at')
    readonly_fields = ('processed_at',)
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin interface for Invoice management."""
    list_display = ('invoice_number', 'client', 'status_badge', 'issue_date', 'due_date',
                    'total_display', 'amount_paid_display', 'balance_display')
    list_filter = ('status', 'issue_date', 'due_date')
    search_fields = ('invoice_number', 'client__user__username', 'client__user__email',
                     'client__company_name', 'description')
    date_hierarchy = 'issue_date'
    
    fieldsets = (
        ('Invoice Information', {
            'fields': ('invoice_number', 'client', 'status')
        }),
        ('Dates', {
            'fields': ('issue_date', 'due_date', 'paid_date')
        }),
        ('Description', {
            'fields': ('description', 'notes')
        }),
        ('Totals', {
            'fields': ('tax_rate', 'subtotal', 'tax_amount', 'total', 'amount_paid')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('invoice_number', 'subtotal', 'tax_amount', 'total', 
                       'amount_paid', 'created_at', 'updated_at')
    
    inlines = [InvoiceLineItemInline, PaymentInline]
    
    actions = ['mark_as_sent', 'mark_as_paid', 'recalculate_totals']
    
    def status_badge(self, obj):
        colors = {
            'draft': '#95a5a6',
            'sent': '#3498db',
            'paid': '#27ae60',
            'overdue': '#e74c3c',
            'cancelled': '#7f8c8d',
        }
        status = obj.status
        if obj.is_overdue() and status not in ['paid', 'cancelled']:
            status = 'overdue'
        
        color = colors.get(status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color, status.upper()
        )
    status_badge.short_description = 'Status'
    
    def total_display(self, obj):
        return format_html('${:,.2f}', obj.total)
    total_display.short_description = 'Total'
    total_display.admin_order_field = 'total'
    
    def amount_paid_display(self, obj):
        if obj.amount_paid > 0:
            return format_html('<span style="color: #27ae60;">${:,.2f}</span>', obj.amount_paid)
        return '$0.00'
    amount_paid_display.short_description = 'Paid'
    amount_paid_display.admin_order_field = 'amount_paid'
    
    def balance_display(self, obj):
        balance = obj.get_balance_due()
        if balance > 0:
            return format_html('<span style="color: #e74c3c;">${:,.2f}</span>', balance)
        return '$0.00'
    balance_display.short_description = 'Balance Due'
    
    def mark_as_sent(self, request, queryset):
        updated = queryset.filter(status='draft').update(status='sent')
        self.message_user(request, f'{updated} invoice(s) marked as sent.')
    mark_as_sent.short_description = 'Mark selected as Sent'
    
    def mark_as_paid(self, request, queryset):
        for invoice in queryset:
            if invoice.status != 'paid':
                invoice.status = 'paid'
                invoice.amount_paid = invoice.total
                if not invoice.paid_date:
                    invoice.paid_date = timezone.now().date()
                invoice.save()
        self.message_user(request, f'{queryset.count()} invoice(s) marked as paid.')
    mark_as_paid.short_description = 'Mark selected as Paid'
    
    def recalculate_totals(self, request, queryset):
        for invoice in queryset:
            invoice.calculate_totals()
        self.message_user(request, f'{queryset.count()} invoice(s) recalculated.')
    recalculate_totals.short_description = 'Recalculate totals'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Admin interface for Payment records."""
    list_display = ('payment_id_short', 'invoice', 'amount_display', 'payment_method',
                    'status_badge', 'processed_at', 'created_at')
    list_filter = ('status', 'payment_method', 'processed_at', 'created_at')
    search_fields = ('payment_id', 'invoice__invoice_number', 'reference_number',
                     'stripe_payment_intent_id', 'stripe_charge_id')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Payment Information', {
            'fields': ('payment_id', 'invoice', 'amount', 'payment_method', 'status')
        }),
        ('Stripe Details', {
            'fields': ('stripe_payment_intent_id', 'stripe_charge_id'),
            'classes': ('collapse',)
        }),
        ('Manual Payment Details', {
            'fields': ('reference_number', 'notes')
        }),
        ('Timestamps', {
            'fields': ('processed_at', 'created_at')
        }),
    )
    
    readonly_fields = ('payment_id', 'processed_at', 'created_at')
    
    def payment_id_short(self, obj):
        return str(obj.payment_id)[:8]
    payment_id_short.short_description = 'Payment ID'
    
    def amount_display(self, obj):
        return format_html('${:,.2f}', obj.amount)
    amount_display.short_description = 'Amount'
    amount_display.admin_order_field = 'amount'
    
    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'processing': '#3498db',
            'succeeded': '#27ae60',
            'failed': '#e74c3c',
            'refunded': '#95a5a6',
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color, obj.status.upper()
        )
    status_badge.short_description = 'Status'


@admin.register(models.InvoiceTemplate)
class InvoiceTemplateAdmin(admin.ModelAdmin):
    """Admin interface for invoice templates."""
    list_display = ('name', 'description', 'default_tax_rate', 'days_until_due', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_active')
        }),
        ('Default Invoice Settings', {
            'fields': ('default_description', 'default_notes', 'default_tax_rate', 'days_until_due')
        }),
        ('Line Items', {
            'fields': ('default_line_items',),
            'description': 'Enter line items as JSON: [{"description": "Service name", "quantity": 1, "unit_price": 100.00}, ...]'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    """Admin interface for Project management."""
    list_display = ('job_number', 'job_name', 'client', 'status', 'billing_type', 
                    'estimated_total', 'due_date', 'created_at')
    list_filter = ('status', 'billing_type', 'created_at')
    search_fields = ('job_number', 'job_name', 'client__first_name', 'client__last_name', 
                     'client__company_name', 'description')
    readonly_fields = ('created_at', 'updated_at', 'created_by')
    date_hierarchy = 'created_at'
    ordering = ['-job_number']
    
    fieldsets = (
        ('Project Identification', {
            'fields': ('job_number', 'job_name', 'description', 'client')
        }),
        ('Timeline', {
            'fields': ('start_date', 'due_date', 'completed_date', 'status')
        }),
        ('Billing Information', {
            'fields': ('billing_type', 'fixed_price', 'hourly_rate', 'estimated_hours', 'actual_hours')
        }),
        ('Notes & Metadata', {
            'fields': ('notes', 'created_by', 'created_at', 'updated_at')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # If creating new project
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

