# Phase 1 Implementation: Expenses & Overdue Reminders

## Summary

This document describes the implementation of Phase 1 enhancements to the Django CRM system:
1. **Expenses Module** - Track and manage business expenses
2. **Overdue Reminders** - Automated email reminders for overdue invoices

## Components Implemented

### 1. Expense Module

#### Models
- **ExpenseCategory**: Categories for organizing expenses (Office Supplies, Travel, Meals, etc.)
  - Name, description, tax deductibility flag, active status
  - Relationships to Expense instances

- **Expense**: Individual business expense records
  - Description, amount, category, date
  - Optional links to Project and Client
  - Vendor information, receipt URL
  - Status tracking (pending, approved, rejected, reimbursed)
  - Tax deductibility and category for accounting
  - Approval workflow with user tracking

#### Views

1. **expense_list** - List all expenses with filtering
   - Filter by: status, category, date range
   - Displays totals and statistics
   - Route: `/billing/expenses/`

2. **expense_detail** - View individual expense
   - Shows all expense information
   - Approval/rejection buttons for staff
   - Receipt link (if provided)
   - Route: `/billing/expenses/<id>/`

3. **expense_create** - Create new expense
   - Form for expense submission
   - Route: `/billing/expenses/create/`

4. **expense_edit** - Edit existing expense
   - Only allowed for pending expenses
   - Route: `/billing/expenses/<id>/edit/`

5. **expense_delete** - Delete expense
   - Route: `/billing/expenses/<id>/delete/`

6. **expense_approve** - Approve expense
   - Staff only action
   - Records approver and approval date
   - Route: `/billing/expenses/<id>/approve/`

7. **expense_report** - Detailed expense reports
   - Group by category, project, or month
   - Date range filtering
   - Tax deductible summary
   - Route: `/billing/expenses/report/`

8. **expense_dashboard** - Overview and analytics (New)
   - Pending expense count and amount
   - Monthly breakdown
   - Category distribution
   - Tax deductibility analysis
   - Route: `/billing/expenses/dashboard/`

#### Forms
- **ExpenseForm**: ModelForm for creating/editing expenses
  - Located in `billing/forms.py`
  - Handles all expense fields

#### Templates

1. **list.html** - Expense list with filters and table
2. **form.html** - Create/edit form with validation
3. **detail.html** - Full expense detail view (New)
4. **report.html** - Expense report and analytics (New)
5. **approve.html** - Approval interface (existing)

#### Admin Interface
- **ExpenseCategoryAdmin**: Manage expense categories
  - List display: name, description, is_active
  - Filterable and searchable

- **ExpenseAdmin**: Manage expenses
  - List display: description, amount, category, status
  - Filtering by: status, category, date
  - Bulk approval action
  - Read-only fields for approved expenses

### 2. Overdue Reminders Module

#### Database Changes
- Added `reminder_sent` field to Invoice model
  - Boolean flag to track if reminder was sent
  - Default: False
  - Migration: `0003_invoice_reminder_sent.py`

#### Management Command

**Command**: `python manage.py send_overdue_reminders`

Location: `billing/management/commands/send_overdue_reminders.py`

Features:
- Finds invoices overdue by 30, 60, and 90 days
- Sends HTML and text email reminders
- Tracks which invoices have received reminders
- Prevents duplicate reminders
- Logs success/failure for each email

Options:
- `--days N`: Send reminders for invoices overdue by specific number of days

Example:
```bash
# Send all default reminders
python manage.py send_overdue_reminders

# Send reminders for invoices overdue by 45 days
python manage.py send_overdue_reminders --days 45
```

#### Email Templates

1. **overdue_reminder.html** - HTML email template
   - Professional formatted reminder
   - Invoice details summary
   - Company contact information
   - Call to action for payment

2. **overdue_reminder.txt** - Plain text alternative
   - Same information as HTML version
   - Used as fallback for text-only email clients

Both templates located in `templates/billing/email/`

#### Scheduling

**Option 1: Cron Job (Recommended for Production)**
```bash
# Schedule to run daily at 8:00 AM
0 8 * * * cd /path/to/project && python manage.py send_overdue_reminders
```

**Option 2: Django-APScheduler (Alternative)**
```python
# Would be configured in settings.py
APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"

def send_overdue_reminders_job():
    from django.core.management import call_command
    call_command('send_overdue_reminders')

# In app's ready() method:
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(send_overdue_reminders_job, 'cron', hour=8, minute=0)
scheduler.start()
```

**Option 3: Celery (For Distributed Systems)**
```python
# tasks.py
from celery import shared_task
from django.core.management import call_command

@shared_task
def send_overdue_reminders_task():
    call_command('send_overdue_reminders')
    
# settings.py
CELERY_BEAT_SCHEDULE = {
    'send-overdue-reminders': {
        'task': 'billing.tasks.send_overdue_reminders_task',
        'schedule': crontab(hour=8, minute=0),  # 8 AM daily
    },
}
```

## Configuration

### System Settings

The command uses `SystemSettings` model for company information:
- `company_name` - Used in email signature
- `phone_number` - Optional phone in email footer

These can be configured in: `/billing/settings/` admin page

### Email Settings

In `settings.py`:
```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'  # or your email provider
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@gmail.com'
EMAIL_HOST_PASSWORD = 'your-app-password'
DEFAULT_FROM_EMAIL = 'billing@yourcompany.com'
```

## Usage Examples

### Creating an Expense

1. Staff navigates to `/billing/expenses/create/`
2. Fills out expense form:
   - Description: "Office Supplies - Paper & Ink"
   - Amount: $127.50
   - Category: Office Supplies
   - Date: 2024-01-15
   - Vendor: Staples
   - Receipt URL: (optional cloud storage link)
   - Tax Deductible: Yes
   - Notes: (optional)
3. Submits form
4. Expense created with status: "pending"

### Approving an Expense

1. Staff reviews expense in list or detail view
2. Clicks "Approve" button
3. System records:
   - Status changes to "approved"
   - Approval user set to current staff member
   - Approval date recorded
4. Email confirmation sent (optional future enhancement)

### Generating Expense Report

1. Navigate to `/billing/expenses/report/`
2. Select date range (optional)
3. Choose grouping: by category, project, or month
4. View breakdown with totals
5. Export to CSV/PDF (can be added)

### Sending Overdue Reminders

**Manual execution:**
```bash
python manage.py send_overdue_reminders
```

**Automated via cron:**
```
# /etc/crontab or crontab -e
0 8 * * * /path/to/venv/bin/python /path/to/manage.py send_overdue_reminders >> /var/log/overdue_reminders.log 2>&1
```

**Testing reminder:**
```bash
# Test for invoices overdue by 30 days (use test data)
python manage.py send_overdue_reminders --days 30
```

## Data Structure

### Expense Fields
```python
Expense:
  - description (CharField, 255)
  - amount (DecimalField, 12,2)
  - category (ForeignKey to ExpenseCategory)
  - expense_date (DateField)
  - submitted_date (DateTimeField, auto)
  - reimbursed_date (DateField, optional)
  - project (ForeignKey, optional)
  - client (ForeignKey, optional)
  - vendor (CharField, optional)
  - receipt_url (URLField, optional)
  - notes (TextField, optional)
  - tax_deductible (BooleanField)
  - tax_category (CharField, optional)
  - status (CharField: pending/approved/rejected/reimbursed)
  - approved_by (ForeignKey to User, optional)
  - approved_date (DateTimeField, optional)
  - created_by (ForeignKey to User)
```

### Invoice Reminder Fields
```python
Invoice:
  - reminder_sent (BooleanField, new)
  - reminder_sent_count (PositiveIntegerField, existing)
  - last_reminder_date (DateTimeField, existing)
```

## Database Migrations

Applied migrations:
1. `0003_invoice_reminder_sent.py` - Adds reminder tracking field to Invoice

To apply migrations:
```bash
python manage.py migrate billing
```

## Testing

### Manual Testing Checklist

**Expense Module:**
- [ ] Create expense with valid data
- [ ] Submit expense with missing required field (should fail)
- [ ] Edit pending expense
- [ ] Attempt to edit approved expense (should be blocked)
- [ ] Delete expense
- [ ] Approve expense as staff
- [ ] Filter expenses by status
- [ ] Filter expenses by date range
- [ ] View expense detail page
- [ ] Access expense list (should require login)

**Overdue Reminders:**
- [ ] Create invoice with due date 30 days ago
- [ ] Run: `python manage.py send_overdue_reminders`
- [ ] Check email received
- [ ] Verify `reminder_sent` flag is True
- [ ] Run again, verify email not sent twice
- [ ] Test with `--days` option
- [ ] Verify text and HTML email versions

### Unit Testing (Optional)

```python
# tests.py
from django.test import TestCase
from .models import Expense, ExpenseCategory, Invoice
from django.contrib.auth.models import User

class ExpenseTestCase(TestCase):
    def setUp(self):
        self.category = ExpenseCategory.objects.create(
            name='Travel',
            is_tax_deductible=True
        )
        self.user = User.objects.create_user('testuser', 'test@example.com', 'password')
        
    def test_create_expense(self):
        expense = Expense.objects.create(
            description='Hotel',
            amount=150.00,
            category=self.category,
            created_by=self.user
        )
        self.assertEqual(expense.status, 'pending')
```

## Security Considerations

1. **Staff Only Access**: All expense views require `@staff_member_required` decorator
2. **Edit Restrictions**: Only pending expenses can be edited
3. **Approval Workflow**: Changes tracked with user who approved
4. **Email Privacy**: Only sends to client's registered email address
5. **Receipt URLs**: Should use secure cloud storage (Dropbox, Google Drive, S3)

## Performance Optimization

1. **Database Queries**:
   - Use `select_related()` for ForeignKey joins
   - Use `prefetch_related()` for reverse relations
   - Add database indexes on frequently filtered fields

2. **Email Sending**:
   - Use batch sending for multiple reminders
   - Consider async task queue (Celery) for high volume

3. **Caching**:
   - Cache expense categories (rarely change)
   - Cache summary totals on dashboard

## Future Enhancements

1. **Bulk Operations**:
   - Bulk import expenses from CSV
   - Bulk reject/approve expenses

2. **Integrations**:
   - QuickBooks/FreshBooks sync
   - Receipt OCR and auto-categorization
   - Automatic approval rules

3. **Advanced Reporting**:
   - Expense trends and forecasting
   - Budget vs. actual analysis
   - Tax category reconciliation

4. **Reminders**:
   - Additional reminder thresholds (15, 45 days)
   - Escalation emails to owner
   - Late payment fees calculation

5. **Mobile**:
   - Mobile app for expense submission
   - Receipt photo capture
   - Real-time notifications

## Support & Troubleshooting

### Common Issues

**Issue**: Reminder emails not sending
- Check EMAIL settings in settings.py
- Verify SystemSettings configured with company info
- Check logs: `python manage.py send_overdue_reminders`
- Test with: `python manage.py shell` and manual mail test

**Issue**: Expense approval fails
- Ensure user has `staff_member` permission
- Check that expense status is 'pending'
- Verify user has "change_expense" permission

**Issue**: Receipt URL not working
- Verify URL is publicly accessible
- Check cloud storage sharing permissions
- Test URL manually in browser

## Files Modified/Created

### Created Files
- `billing/management/commands/send_overdue_reminders.py`
- `billing/management/__init__.py`
- `billing/management/commands/__init__.py`
- `billing/migrations/0003_invoice_reminder_sent.py`
- `templates/billing/expenses/detail.html` (new)
- `templates/billing/expenses/report.html` (new)
- `templates/billing/email/overdue_reminder.html`
- `templates/billing/email/overdue_reminder.txt`

### Modified Files
- `billing/models.py` - Added `reminder_sent` field to Invoice
- `billing/views.py` - Added `expense_dashboard()` view
- `billing/urls.py` - Added expense dashboard route
- `billing/forms.py` - (already had ExpenseForm)
- `billing/admin.py` - (already registered Expense models)

## Deployment Steps

1. **Development**:
   ```bash
   git commit -m "Phase 1: Implement Expenses & Overdue Reminders"
   python manage.py migrate
   python manage.py test
   ```

2. **Staging**:
   ```bash
   git pull origin phase-1
   python manage.py migrate
   python manage.py send_overdue_reminders  # Test run
   ```

3. **Production**:
   ```bash
   git pull origin phase-1
   python manage.py migrate --no-input
   # Add cron job for reminders
   echo "0 8 * * * /path/to/venv/bin/python /path/to/manage.py send_overdue_reminders" | crontab -
   ```

## References

- Django Management Commands: https://docs.djangoproject.com/en/4.2/howto/custom-management-commands/
- Django Models: https://docs.djangoproject.com/en/4.2/topics/db/models/
- Email: https://docs.djangoproject.com/en/4.2/topics/email/
- Cron Jobs: https://en.wikipedia.org/wiki/Cron

---

**Implementation Date**: 2024
**Version**: 1.0
**Status**: Complete
