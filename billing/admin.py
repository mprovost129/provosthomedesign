from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from .models import Client, Invoice, InvoiceLineItem, Payment


class InvoiceLineItemInline(admin.TabularInline):
    """Inline editing of invoice line items."""
    model = InvoiceLineItem
    extra = 1
    fields = ('description', 'quantity', 'unit_price', 'total', 'related_plan', 'order')
    readonly_fields = ('total',)
    ordering = ['order', 'id']


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """Admin interface for Client management."""
    list_display = ('user_display', 'company_name', 'phone', 'city', 'state', 
                    'total_invoices', 'total_outstanding', 'created_at')
    list_filter = ('state', 'created_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name',
                     'company_name', 'phone')
    
    fieldsets = (
        ('User Account', {
            'fields': ('user',)
        }),
        ('Company Information', {
            'fields': ('company_name', 'phone')
        }),
        ('Address', {
            'fields': ('address_line1', 'address_line2', 'city', 'state', 'zip_code')
        }),
        ('Payment Integration', {
            'fields': ('stripe_customer_id',),
            'classes': ('collapse',)
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
    
    def user_display(self, obj):
        return obj.user.get_full_name() or obj.user.username
    user_display.short_description = 'Client Name'
    
    def total_invoices(self, obj):
        count = obj.invoices.count()
        if count:
            url = reverse('admin:billing_invoice_changelist') + f'?client__id__exact={obj.id}'
            return format_html('<a href="{}">{} invoices</a>', url, count)
        return '0 invoices'
    total_invoices.short_description = 'Invoices'
    
    def total_outstanding(self, obj):
        from decimal import Decimal
        outstanding = Decimal('0.00')
        for inv in obj.invoices.exclude(status='paid'):
            outstanding += inv.get_balance_due()
        if outstanding > 0:
            return format_html('<span style="color: #d63031;">${:,.2f}</span>', outstanding)
        return '$0.00'
    total_outstanding.short_description = 'Outstanding'


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
