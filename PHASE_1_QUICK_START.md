# Quick Start Guide: Phase 1 Features

## Expenses Module - Quick Start

### 1. Access the Expenses Section

As a staff member, you can access expenses at:
- List: `http://localhost:8000/billing/expenses/`
- Create: `http://localhost:8000/billing/expenses/create/`
- Dashboard: `http://localhost:8000/billing/expenses/dashboard/`
- Report: `http://localhost:8000/billing/expenses/report/`

### 2. Create an Expense

```
1. Go to Expenses > Create
2. Fill in the form:
   ✓ Description: What was purchased?
   ✓ Amount: $XX.XX
   ✓ Category: Select from dropdown
   ✓ Date: When was it purchased?
   ✓ Vendor: Where was it purchased? (optional)
   ✓ Receipt: Link to receipt in cloud storage (optional)
   ✓ Tax Deductible: Yes/No
   ✓ Notes: Any additional info (optional)
3. Click "Save" to submit
```

### 3. Approve Expenses

```
1. Go to Expenses > List
2. Find the expense with status "Pending"
3. Click the expense to view details
4. Click "Approve" button (staff only)
5. System records:
   - Status: Changed to "Approved"
   - Approved by: Your name
   - Approved date: Current date/time
```

### 4. View Expense Dashboard

```
Dashboard shows:
- Pending count and amount
- Total expenses
- This month's spending
- Breakdown by category
- Monthly trends (last 6 months)
- Tax deductible summary
```

### 5. Generate Expense Report

```
1. Go to Expenses > Report
2. Select optional filters:
   - Start Date
   - End Date
   - Group By: Category / Project / Month
3. View breakdown table
4. Summary shows:
   - Grand total
   - Count of expenses
   - Categories used
```

## Overdue Reminders - Quick Start

### 1. Automatic Reminder Setup

**Option A: Using Cron (Linux/Mac)**

Add to crontab:
```bash
# Edit crontab
crontab -e

# Add this line to send reminders daily at 8 AM
0 8 * * * /path/to/venv/bin/python /path/to/manage.py send_overdue_reminders
```

**Option B: Manual Testing**

```bash
# Test the command
python manage.py send_overdue_reminders

# Test for specific days overdue
python manage.py send_overdue_reminders --days 30
```

### 2. How It Works

The system automatically:
1. **Checks daily** for invoices overdue by 30, 60, and 90 days
2. **Sends email** to client with invoice details
3. **Marks reminder** as sent to prevent duplicates
4. **Logs results** for troubleshooting

### 3. Email Content

Overdue reminder emails include:
- Professional greeting to client
- Invoice number and amount
- Days overdue (highlighted)
- Due date
- Company contact information
- Request for payment

### 4. Configuration

Before reminders can send, ensure:

**Email Settings** in `settings.py`:
```python
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@example.com'
EMAIL_HOST_PASSWORD = 'your-app-password'
```

**Company Settings** in Admin:
1. Go to Admin > System Settings
2. Set:
   - Company Name
   - Phone Number (optional)
3. Save

### 5. Tracking Reminders

Check which invoices received reminders:
1. Go to Invoices list
2. Look for Invoice with:
   - Status: "Issued" or "Partial"
   - `reminder_sent` = True (admin view)
   - `last_reminder_date` populated

## Helpful Tips

### For Expense Managers

- **Tax Deductible**: Mark as "No" for personal reimbursements
- **Receipt URL**: Use Dropbox share link or Google Drive share link
- **Category**: Consistent categorization helps with tax planning
- **Project Link**: Link expenses to projects for cost tracking

### For Finance Managers

- **Dashboard Review**: Check daily for pending expense approvals
- **Monthly Reports**: Generate reports for accounting reconciliation
- **Tax Planning**: Use "Tax Deductible" filter for year-end review
- **Budget Tracking**: Link expenses to projects to track project costs

### For Company Owners

- **Expense Trends**: Dashboard shows spending patterns
- **Cash Flow**: Monitor expense approval status
- **Reminders**: System handles invoice follow-up automatically
- **Reports**: Use monthly/category reports for business analysis

## Common Workflows

### Workflow 1: Expense Reimbursement

```
1. Employee submits expense
   ✓ Receipt attached
   ✓ Tax category noted
   ✓ Project linked
   
2. Manager reviews
   ✓ Amount reasonable
   ✓ Receipt valid
   ✓ Tax category correct
   
3. Manager approves
   ✓ Status changes to "Approved"
   ✓ Ready for accounting
   
4. Accounting processes
   ✓ Records in accounting system
   ✓ Creates check/transfer
   ✓ Marks as "Reimbursed"
```

### Workflow 2: Invoice Follow-up

```
Day 1:   Invoice issued (Due in 30 days)
Day 30:  Automatically marked overdue, reminder sent
Day 60:  Second reminder sent (optional escalation)
Day 90:  Final reminder sent (optional legal notice)
Day 120: Manual follow-up required
```

## Troubleshooting

### Expense Issues

**Q: Can't edit a submitted expense?**
A: Only pending expenses can be edited. Approved/reimbursed expenses are locked.

**Q: Receipt link not working?**
A: Ensure the cloud storage link is shared publicly or use view-only link.

**Q: Expense not showing in reports?**
A: Check the expense date is within report date range.

### Reminder Issues

**Q: Reminders not being sent?**
A: Check:
- Email settings configured in settings.py
- Company settings filled in admin
- Invoices have client email address
- Due date is exactly 30/60/90 days ago

**Q: Getting duplicate reminders?**
A: The system prevents this automatically. Check `reminder_sent` flag.

**Q: Want to send reminders now instead of waiting for cron?**
A: Run: `python manage.py send_overdue_reminders`

## Admin Panel Access

**Expense Management**:
- Admin > Billing > Expense Categories (manage categories)
- Admin > Billing > Expenses (view/approve all)

**Invoice Management**:
- Admin > Billing > Invoices (view/manage all)

## Database Queries (for Admin)

### Find pending expenses over $500
```python
from billing.models import Expense
expensive_pending = Expense.objects.filter(
    status='pending',
    amount__gt=500
).order_by('-amount')
```

### Find invoices that got reminder sent
```python
from billing.models import Invoice
reminded = Invoice.objects.filter(reminder_sent=True).order_by('-last_reminder_date')
```

### Calculate monthly expense total
```python
from billing.models import Expense
from django.db.models import Sum
from datetime import date

current_month = Expense.objects.filter(
    expense_date__year=2024,
    expense_date__month=1
).aggregate(total=Sum('amount'))['total']
```

## Next Steps After Phase 1

- Phase 2: Additional reminder thresholds and escalation
- Phase 3: Bulk expense import from CSV
- Phase 4: QuickBooks/FreshBooks integration
- Phase 5: Mobile app for expense photo capture

---

**Need help?** Check PHASE_1_IMPLEMENTATION.md for detailed documentation.
