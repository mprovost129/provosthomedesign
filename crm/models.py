from django.db import models
from django.utils import timezone


class Client(models.Model):
    """A client/customer who may have multiple projects."""
    
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=50, blank=True, null=True)
    zip_code = models.CharField(max_length=20, blank=True, null=True)
    
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('inactive', 'Inactive'),
            ('prospect', 'Prospect'),
        ],
        default='active'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'
    
    def __str__(self):
        return self.name
    
    @property
    def total_revenue(self):
        """Sum of all paid invoices for this client."""
        from django.db.models import Sum
        return (
            Invoice.objects.filter(project__client=self, status='paid')
            .aggregate(total=Sum('amount'))['total'] or 0
        )
    
    @property
    def total_expenses(self):
        """Sum of all expenses for this client."""
        from django.db.models import Sum
        return (
            Expense.objects.filter(client=self)
            .aggregate(total=Sum('amount'))['total'] or 0
        )


class Project(models.Model):
    """A project belongs to a client and may have multiple invoices."""
    
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('completed', 'Completed'),
            ('on_hold', 'On Hold'),
            ('cancelled', 'Cancelled'),
        ],
        default='active'
    )
    
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    
    budget = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
    
    def __str__(self):
        return f"{self.name} ({self.client.name})"
    
    @property
    def total_revenue(self):
        """Sum of all paid invoices for this project."""
        from django.db.models import Sum
        return (
            Invoice.objects.filter(project=self, status='paid')
            .aggregate(total=Sum('amount'))['total'] or 0
        )
    
    @property
    def total_expenses(self):
        """Sum of all expenses for this project."""
        from django.db.models import Sum
        return (
            Expense.objects.filter(project=self)
            .aggregate(total=Sum('amount'))['total'] or 0
        )
    
    @property
    def profit(self):
        """Revenue minus expenses."""
        return self.total_revenue - self.total_expenses
    
    @property
    def profit_margin(self):
        """Profit margin percentage."""
        if self.total_revenue == 0:
            return 0
        return round((self.profit / self.total_revenue) * 100, 2)


class Invoice(models.Model):
    """An invoice issued for a project."""
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='invoices')
    
    number = models.CharField(max_length=50, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True, null=True)
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('viewed', 'Viewed'),
            ('paid', 'Paid'),
            ('overdue', 'Overdue'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft'
    )
    
    issued_date = models.DateField(default=timezone.now)
    due_date = models.DateField()
    paid_date = models.DateField(blank=True, null=True)
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-issued_date']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
    
    def __str__(self):
        return f"Invoice {self.number} - {self.project.client.name}"
    
    @property
    def is_overdue(self):
        """Check if invoice is past due date and not paid."""
        if self.status == 'paid':
            return False
        return self.due_date < timezone.now().date()
    
    def mark_paid(self):
        """Mark invoice as paid."""
        self.status = 'paid'
        self.paid_date = timezone.now().date()
        self.save()


class Expense(models.Model):
    """An expense tied to a client and optionally a project."""
    
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='expenses')
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, blank=True, null=True, related_name='expenses')
    
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    category = models.CharField(
        max_length=50,
        choices=[
            ('labor', 'Labor'),
            ('materials', 'Materials'),
            ('equipment', 'Equipment'),
            ('permits', 'Permits'),
            ('subcontractors', 'Subcontractors'),
            ('travel', 'Travel'),
            ('other', 'Other'),
        ],
        default='other'
    )
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('reimbursed', 'Reimbursed'),
        ],
        default='pending'
    )
    
    expense_date = models.DateField(default=timezone.now)
    receipt_url = models.URLField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-expense_date']
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'
    
    def __str__(self):
        project_str = f" - {self.project.name}" if self.project else ""
        return f"{self.description} ({self.client.name}{project_str})"


class Transaction(models.Model):
    """Track payment transactions for invoices."""
    
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='transactions')
    
    TRANSACTION_TYPES = [
        ('payment', 'Payment'),
        ('credit', 'Credit'),
        ('refund', 'Refund'),
    ]
    
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    method = models.CharField(
        max_length=50,
        choices=[
            ('check', 'Check'),
            ('ach', 'ACH Transfer'),
            ('card', 'Credit Card'),
            ('cash', 'Cash'),
            ('other', 'Other'),
        ],
        default='other'
    )
    
    transaction_date = models.DateField(default=timezone.now)
    reference_number = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-transaction_date']
        verbose_name = 'Transaction'
        verbose_name_plural = 'Transactions'
    
    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.invoice.number} - ${self.amount}"
