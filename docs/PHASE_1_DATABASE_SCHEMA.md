# Phase 1 Database Schema

## New Models & Fields

### ExpenseCategory Model
```
Table: billing_expensecategory

Columns:
  id                    (AUTO PRIMARY KEY)
  name                  (VARCHAR(100), UNIQUE)
  description           (TEXT)
  is_tax_deductible     (BOOLEAN, DEFAULT=True)
  is_active             (BOOLEAN, DEFAULT=True)
  created_at            (DATETIME, AUTO_NOW_ADD)
  updated_at            (DATETIME, AUTO_NOW)

Indexes:
  - name (UNIQUE)
  - created_at
  
Constraints:
  - name is UNIQUE
```

### Expense Model
```
Table: billing_expense

Columns:
  id                    (AUTO PRIMARY KEY)
  description           (VARCHAR(255))
  amount                (DECIMAL(12,2))
  category_id           (FK → billing_expensecategory)
  expense_date          (DATE)
  submitted_date        (DATETIME, AUTO_NOW_ADD)
  reimbursed_date       (DATE, NULL)
  project_id            (FK → billing_project, NULL)
  client_id             (FK → billing_client, NULL)
  vendor                (VARCHAR(200), NULL)
  receipt_url           (VARCHAR(200), NULL)
  notes                 (TEXT)
  tax_deductible        (BOOLEAN, DEFAULT=True)
  tax_category          (VARCHAR(100), NULL)
  status                (VARCHAR(20), DEFAULT='pending')
                        (Choices: pending, approved, rejected, reimbursed)
  approved_by_id        (FK → auth_user, NULL)
  approved_date         (DATETIME, NULL)
  created_by_id         (FK → auth_user)
  created_at            (DATETIME, AUTO_NOW_ADD)
  updated_at            (DATETIME, AUTO_NOW)

Indexes:
  - expense_date DESC
  - status
  - project_id
  - client_id
  - category_id
  
Foreign Keys:
  - category_id → billing_expensecategory.id
  - project_id → billing_project.id (optional)
  - client_id → billing_client.id (optional)
  - approved_by_id → auth_user.id (optional)
  - created_by_id → auth_user.id
  
Constraints:
  - amount > 0.01
```

### Invoice Model Changes
```
Table: billing_invoice

New Columns:
  reminder_sent         (BOOLEAN, DEFAULT=False)
  
Existing Columns (for reference):
  id                    (AUTO PRIMARY KEY)
  invoice_number        (VARCHAR(50), UNIQUE)
  client_id             (FK → billing_client)
  project_id            (FK → billing_project, NULL)
  status                (VARCHAR(20))
  issue_date            (DATE)
  due_date              (DATE)
  paid_date             (DATE, NULL)
  description           (TEXT)
  notes                 (TEXT)
  subtotal              (DECIMAL(10,2))
  tax_rate              (DECIMAL(5,2))
  tax_amount            (DECIMAL(10,2))
  total                 (DECIMAL(10,2))
  amount_paid           (DECIMAL(10,2))
  payment_token         (UUID, UNIQUE)
  email_sent_date       (DATETIME, NULL)
  email_sent_count      (INT, DEFAULT=0)
  viewed_date           (DATETIME, NULL)
  reminder_sent_count   (INT, DEFAULT=0)
  last_reminder_date    (DATETIME, NULL)
  created_at            (DATETIME, AUTO_NOW_ADD)
  updated_at            (DATETIME, AUTO_NOW)
```

## Database Views (Optional - for Reporting)

### Expense Summary by Category
```sql
CREATE VIEW expense_summary_by_category AS
SELECT 
  ec.name as category_name,
  COUNT(e.id) as expense_count,
  SUM(e.amount) as total_amount,
  SUM(CASE WHEN e.status = 'approved' THEN e.amount ELSE 0 END) as approved_amount,
  SUM(CASE WHEN e.status = 'pending' THEN e.amount ELSE 0 END) as pending_amount,
  SUM(CASE WHEN e.tax_deductible = TRUE THEN e.amount ELSE 0 END) as tax_deductible_amount
FROM billing_expensecategory ec
LEFT JOIN billing_expense e ON ec.id = e.category_id
GROUP BY ec.id, ec.name
ORDER BY total_amount DESC;
```

### Monthly Expense Trend
```sql
CREATE VIEW monthly_expense_trend AS
SELECT 
  DATE_TRUNC('month', expense_date) as month,
  COUNT(*) as expense_count,
  SUM(amount) as total_amount,
  AVG(amount) as avg_expense,
  MAX(amount) as max_expense
FROM billing_expense
GROUP BY DATE_TRUNC('month', expense_date)
ORDER BY month DESC;
```

### Overdue Invoice Summary
```sql
CREATE VIEW overdue_invoice_summary AS
SELECT 
  client_id,
  COUNT(*) as overdue_count,
  SUM(total - amount_paid) as total_due,
  MIN(due_date) as oldest_due_date,
  reminder_sent,
  COUNT(CASE WHEN reminder_sent = TRUE THEN 1 END) as reminded_count
FROM billing_invoice
WHERE status IN ('issued', 'partial')
  AND due_date < CURRENT_DATE
GROUP BY client_id, reminder_sent
ORDER BY total_due DESC;
```

## Relationships Diagram

```
ExpenseCategory
    ├─ 1:N → Expense
    
Expense
    ├─ M:1 → Client
    ├─ M:1 → Project
    ├─ M:1 → User (created_by)
    └─ M:1 → User (approved_by)

Invoice
    ├─ M:1 → Client
    ├─ M:1 → Project
    └─ (New) reminder_sent field for tracking

Client
    ├─ 1:N → Expense
    └─ 1:N → Invoice
```

## Query Performance Tips

### Expensive Queries (Avoid)

```python
# BAD: N+1 query problem
expenses = Expense.objects.all()
for expense in expenses:
    print(expense.category.name)  # Extra query each iteration
    print(expense.created_by.get_full_name())  # Extra query
```

### Optimized Queries (Prefer)

```python
# GOOD: Use select_related for ForeignKey
expenses = Expense.objects.select_related('category', 'created_by').all()
for expense in expenses:
    print(expense.category.name)  # No extra query
    print(expense.created_by.get_full_name())  # No extra query

# GOOD: Use prefetch_related for reverse FK
categories = ExpenseCategory.objects.prefetch_related('expenses').all()
for cat in categories:
    for exp in cat.expenses.all():  # No extra query
        print(exp.description)

# GOOD: Use aggregation for summaries
from django.db.models import Sum
totals = Expense.objects.filter(
    status='approved'
).aggregate(
    total_amount=Sum('amount'),
    count=Count('id')
)
```

## Data Import/Export

### SQL Backup
```bash
# PostgreSQL
pg_dump -U user dbname > backup.sql

# MySQL
mysqldump -u user -p dbname > backup.sql

# SQLite (for development)
cp db.sqlite3 db.sqlite3.backup
```

### Export Expenses to CSV
```python
import csv
from billing.models import Expense

expenses = Expense.objects.all().values(
    'description', 'amount', 'category__name', 
    'expense_date', 'status'
)

with open('expenses.csv', 'w') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'description', 'amount', 'category__name',
        'expense_date', 'status'
    ])
    writer.writeheader()
    for exp in expenses:
        writer.writerow(exp)
```

### Import Expenses from CSV
```python
import csv
from billing.models import Expense, ExpenseCategory
from django.contrib.auth.models import User

user = User.objects.get(username='admin')

with open('expenses.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        category = ExpenseCategory.objects.get(name=row['category__name'])
        Expense.objects.create(
            description=row['description'],
            amount=row['amount'],
            category=category,
            expense_date=row['expense_date'],
            status=row['status'],
            created_by=user
        )
```

## Migration Path

### Step 1: Create Expense Models
Migration: `0001_initial` (already includes ExpenseCategory and Expense)

### Step 2: Add Reminder Field to Invoice
Migration: `0003_invoice_reminder_sent`

### Step 3: Run Migrations
```bash
python manage.py migrate billing
```

### Step 4: Verify Data
```bash
python manage.py shell
>>> from billing.models import Expense, Invoice
>>> Expense.objects.count()
>>> Invoice.objects.filter(reminder_sent=False).count()
```

## Data Validation Rules

### Expense Validation
- `amount` must be > $0.01
- `description` required (non-empty)
- `category` required (must exist)
- `expense_date` required
- `expense_date` cannot be in future
- `created_by` required (auto-set from request.user)

### Invoice Reminder Rules
- Only checks invoices with status 'issued' or 'partial'
- Only sends reminders for unpaid invoices
- Only sends reminder if `reminder_sent` = False
- Sets `reminder_sent` = True after sending
- Updates `last_reminder_date` with send timestamp

## Indexes for Performance

### Recommended Indexes (already created)

```sql
-- Expense indexes
CREATE INDEX idx_expense_date ON billing_expense(expense_date DESC);
CREATE INDEX idx_expense_status ON billing_expense(status);
CREATE INDEX idx_expense_project ON billing_expense(project_id);
CREATE INDEX idx_expense_client ON billing_expense(client_id);
CREATE INDEX idx_expense_category ON billing_expense(category_id);

-- Invoice indexes (existing)
CREATE INDEX idx_invoice_client_status ON billing_invoice(client_id, status);
CREATE INDEX idx_invoice_due_date ON billing_invoice(due_date);

-- Composite indexes for common queries
CREATE INDEX idx_expense_date_status ON billing_expense(expense_date DESC, status);
CREATE INDEX idx_invoice_due_status ON billing_invoice(due_date, status);
```

## Statistics & Reporting Queries

### Get Monthly Expense Report
```python
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from billing.models import Expense

monthly = Expense.objects.annotate(
    month=TruncMonth('expense_date')
).values('month').annotate(
    total=Sum('amount'),
    count=Count('id')
).order_by('-month')

for item in monthly:
    print(f"{item['month']}: ${item['total']} ({item['count']} expenses)")
```

### Get Category Distribution
```python
category_stats = Expense.objects.values('category__name').annotate(
    count=Count('id'),
    total=Sum('amount'),
    avg=Avg('amount')
).order_by('-total')
```

### Get Pending Approval Amount
```python
pending = Expense.objects.filter(
    status='pending'
).aggregate(
    total=Sum('amount'),
    count=Count('id')
)

print(f"${pending['total']} pending ({pending['count']} expenses)")
```

---

**Database Version**: Django 4.2+
**Created**: 2024
**Last Updated**: 2024
