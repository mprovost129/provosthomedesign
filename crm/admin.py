from django.contrib import admin
from .models import Client, Project, Invoice, Expense, Transaction


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'email', 'phone')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'email', 'phone', 'status')
        }),
        ('Address', {
            'fields': ('address', 'city', 'state', 'zip_code'),
            'classes': ('collapse',)
        }),
        ('Additional', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'client', 'status', 'start_date', 'end_date')
    list_filter = ('status', 'start_date', 'client')
    search_fields = ('name', 'client__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Project Info', {
            'fields': ('client', 'name', 'status', 'description')
        }),
        ('Timeline', {
            'fields': ('start_date', 'end_date')
        }),
        ('Budget', {
            'fields': ('budget',),
            'classes': ('collapse',)
        }),
        ('Additional', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('number', 'project', 'amount', 'status', 'issued_date', 'due_date', 'paid_date')
    list_filter = ('status', 'issued_date', 'due_date', 'project__client')
    search_fields = ('number', 'project__name', 'project__client__name')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['mark_as_paid']
    
    fieldsets = (
        ('Invoice Info', {
            'fields': ('project', 'number', 'amount', 'status')
        }),
        ('Dates', {
            'fields': ('issued_date', 'due_date', 'paid_date')
        }),
        ('Details', {
            'fields': ('description', 'notes'),
            'classes': ('collapse',)
        }),
        ('System', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def mark_as_paid(self, request, queryset):
        for invoice in queryset:
            invoice.mark_paid()
        self.message_user(request, f"{queryset.count()} invoice(s) marked as paid")
    mark_as_paid.short_description = "Mark selected invoices as paid"


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('description', 'client', 'project', 'amount', 'category', 'status', 'expense_date')
    list_filter = ('status', 'category', 'expense_date', 'client', 'project')
    search_fields = ('description', 'client__name', 'project__name')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['mark_as_approved', 'mark_as_rejected']
    
    fieldsets = (
        ('Expense Info', {
            'fields': ('client', 'project', 'description', 'category')
        }),
        ('Amount & Status', {
            'fields': ('amount', 'status')
        }),
        ('Tracking', {
            'fields': ('expense_date', 'receipt_url', 'notes'),
            'classes': ('collapse',)
        }),
        ('System', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def mark_as_approved(self, request, queryset):
        queryset.update(status='approved')
        self.message_user(request, f"{queryset.count()} expense(s) marked as approved")
    mark_as_approved.short_description = "Mark as approved"
    
    def mark_as_rejected(self, request, queryset):
        queryset.update(status='rejected')
        self.message_user(request, f"{queryset.count()} expense(s) marked as rejected")
    mark_as_rejected.short_description = "Mark as rejected"


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'transaction_type', 'amount', 'method', 'transaction_date')
    list_filter = ('transaction_type', 'method', 'transaction_date')
    search_fields = ('invoice__number', 'reference_number')
    readonly_fields = ('created_at',)
    
    fieldsets = (
        ('Transaction Info', {
            'fields': ('invoice', 'transaction_type', 'amount', 'method')
        }),
        ('Details', {
            'fields': ('transaction_date', 'reference_number', 'notes')
        }),
        ('System', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
