# Phase 1 Architecture Diagram

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        DJANGO CRM SYSTEM                        │
│                      Phase 1 Architecture                        │
└─────────────────────────────────────────────────────────────────┘

                          ┌─────────────────┐
                          │   Web Browser   │
                          └────────┬────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │   Django Web Server      │
                    │  (runserver/Gunicorn)   │
                    └──────────┬───────────────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
         ┌──────────┐  ┌──────────┐  ┌──────────┐
         │ Expenses │  │ Invoices │  │ Clients  │
         │  Module  │  │  Module  │  │  Module  │
         └──────┬───┘  └──────┬───┘  └────┬─────┘
                │             │            │
                └─────────────┼────────────┘
                              ▼
                    ┌──────────────────────┐
                    │   Models & Views     │
                    │   Forms & Templates  │
                    │   Admin Interface    │
                    └──────────┬───────────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
        ┌────────────┐ ┌───────────┐ ┌─────────────┐
        │  Database  │ │   Email   │ │  Static/   │
        │(PostgreSQL)│ │   Server  │ │   Media    │
        └────────────┘ └───────────┘ └─────────────┘
```

## Expense Module Architecture

```
┌─────────────────────────────────────────────────┐
│         EXPENSE MODULE                          │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌─────────────────────────────────────────┐  │
│  │ Django Views (billing/views.py)          │  │
│  ├─────────────────────────────────────────┤  │
│  │ • expense_list()                         │  │
│  │ • expense_detail()                       │  │
│  │ • expense_create()                       │  │
│  │ • expense_edit()                         │  │
│  │ • expense_delete()                       │  │
│  │ • expense_approve()                      │  │
│  │ • expense_report()                       │  │
│  │ • expense_dashboard() ← NEW              │  │
│  └─────────────────────────────────────────┘  │
│                    │                           │
│                    ▼                           │
│  ┌─────────────────────────────────────────┐  │
│  │ Forms (billing/forms.py)                 │  │
│  ├─────────────────────────────────────────┤  │
│  │ • ExpenseForm (ModelForm)                │  │
│  │   - Validates all fields                 │  │
│  │   - Ensures amount > $0.01               │  │
│  │   - Required field checking              │  │
│  └─────────────────────────────────────────┘  │
│                    │                           │
│                    ▼                           │
│  ┌─────────────────────────────────────────┐  │
│  │ Models (billing/models.py)               │  │
│  ├─────────────────────────────────────────┤  │
│  │ ┌────────────────────────────────────┐  │  │
│  │ │ ExpenseCategory                    │  │  │
│  │ ├────────────────────────────────────┤  │  │
│  │ │ - name (CharField)                 │  │  │
│  │ │ - is_tax_deductible                │  │  │
│  │ │ - is_active                        │  │  │
│  │ └────────────────────────────────────┘  │  │
│  │ ┌────────────────────────────────────┐  │  │
│  │ │ Expense                            │  │  │
│  │ ├────────────────────────────────────┤  │  │
│  │ │ - description                      │  │  │
│  │ │ - amount (DecimalField)            │  │  │
│  │ │ - category (FK)                    │  │  │
│  │ │ - expense_date                     │  │  │
│  │ │ - status (pending/approved/...)    │  │  │
│  │ │ - approved_by (FK, nullable)       │  │  │
│  │ │ - approved_date                    │  │  │
│  │ │ - tax_deductible                   │  │  │
│  │ │ - project (FK, optional)           │  │  │
│  │ │ - client (FK, optional)            │  │  │
│  │ │ - receipt_url                      │  │  │
│  │ │ - notes                            │  │  │
│  │ └────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────┘  │
│                    │                           │
│                    ▼                           │
│  ┌─────────────────────────────────────────┐  │
│  │ Admin Interface (billing/admin.py)       │  │
│  ├─────────────────────────────────────────┤  │
│  │ • ExpenseCategoryAdmin                   │  │
│  │ • ExpenseAdmin (with bulk actions)       │  │
│  │   - Filtering                            │  │
│  │   - Searching                            │  │
│  │   - Bulk approval                        │  │
│  └─────────────────────────────────────────┘  │
│                                                 │
└─────────────────────────────────────────────────┘
```

## Overdue Reminders Architecture

```
┌──────────────────────────────────────────────────┐
│      OVERDUE REMINDERS MODULE                    │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ Scheduler (Cron/APScheduler/Celery)        │ │
│  ├────────────────────────────────────────────┤ │
│  │ Daily at 8:00 AM:                          │ │
│  │ python manage.py send_overdue_reminders    │ │
│  └────────────┬───────────────────────────────┘ │
│               │                                  │
│               ▼                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ Management Command                         │ │
│  │ (billing/management/commands/              │ │
│  │  send_overdue_reminders.py)                │ │
│  ├────────────────────────────────────────────┤ │
│  │ • Finds overdue invoices (30/60/90 days)  │ │
│  │ • Checks reminder_sent flag                │ │
│  │ • Prevents duplicates                      │ │
│  │ • Sends email via SMTP                     │ │
│  │ • Updates Invoice.reminder_sent            │ │
│  │ • Logs success/failure                     │ │
│  └────────────┬───────────────────────────────┘ │
│               │                                  │
│      ┌────────┴────────┐                        │
│      ▼                 ▼                        │
│  ┌────────────┐  ┌──────────────┐             │
│  │  Invoice   │  │ Email Config │             │
│  │  Models    │  │  (settings)  │             │
│  │  reminder_ │  │              │             │
│  │  sent=True │  │  SMTP Server │             │
│  └────────────┘  └──────────────┘             │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ Email Templates                            │ │
│  ├────────────────────────────────────────────┤ │
│  │ • overdue_reminder.html (HTML version)     │ │
│  │ • overdue_reminder.txt (Text version)      │ │
│  │                                            │ │
│  │ Content:                                   │ │
│  │ ├─ Invoice number & amount                 │ │
│  │ ├─ Days overdue (highlighted)              │ │
│  │ ├─ Due date                                │ │
│  │ ├─ Company info                            │ │
│  │ └─ Call to action                          │ │
│  └────────────┬───────────────────────────────┘ │
│               │                                  │
│               ▼                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ Email Sending via SMTP                     │ │
│  ├────────────────────────────────────────────┤ │
│  │ django.core.mail.EmailMessage              │ │
│  │ + render_to_string(templates)              │ │
│  │ = Professional email delivery              │ │
│  └────────────┬───────────────────────────────┘ │
│               │                                  │
│               ▼                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ Client Inbox                               │ │
│  ├────────────────────────────────────────────┤ │
│  │ Invoice Reminder Email                     │ │
│  │ (HTML formatted with company branding)     │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
└──────────────────────────────────────────────────┘
```

## Data Flow Diagram

### Expense Creation Flow
```
User              Form              View              Model             Database
  │                │                 │                │                  │
  ├─ Fill form ───>│                 │                │                  │
  │                │                 │                │                  │
  │                ├─ POST /create ──>│                │                  │
  │                │                 ├─ Validate ────>│                  │
  │                │                 │                ├─ Create ────────>│
  │                │                 │                │                  │
  │                │                 │<─ Return ID ───│<─ Saved ────────│
  │                │                 │                │                  │
  │                │<─ Redirect ─────│                │                  │
  │                │                 │                │                  │
  │<─ Success ─────│                 │                │                  │
```

### Approval Flow
```
Approver          View              Model            Database           Email
   │               │                 │                │                  │
   ├─ Click ───────────────────────>│                 │                  │
   │ Approve       │                 │ expense.       │                  │
   │               │                 │ approve(user)  │                  │
   │               │                 ├─ Update ─────>│                  │
   │               │                 │ status         │                  │
   │               │                 │ approved_by    │                  │
   │               │                 │ approved_date  │                  │
   │               │<─ Refresh ──────│                │                  │
   │               │                 │                │                  │
   │<─ Updated ────│                 │                │                  │
   │               │                 │                │                  │
   │               │ (Optional future enhancement: send email notification)
```

### Reminder Generation Flow
```
Scheduler         Command           Database         Email          Client
    │               │                │                 │              │
    ├─ 8:00 AM ────>│                │                 │              │
    │               │ Query invoices │                 │              │
    │               │ (due 30/60/90  │                 │              │
    │               │ days ago)      │                 │              │
    │               ├─ Find unpaid ─>│                 │              │
    │               │<─ Results ─────│                 │              │
    │               │                │                 │              │
    │               ├─ Render template                 │              │
    │               │ with data      │                 │              │
    │               │                │                 │              │
    │               ├─ Send email ───────────────────>│              │
    │               │                │                 ├─ To Inbox ->│
    │               │                │                 │              │
    │               ├─ Update        │                 │              │
    │               │ reminder_sent= │                 │              │
    │               │ True          >│                 │              │
    │               │                │                 │              │
    │<─ Complete ───│                │                 │              │
```

## Request/Response Cycle

### Typical Expense View Request
```
HTTP Request
  ├─ URL: /billing/expenses/
  ├─ Method: GET
  └─ User: Authenticated Staff Member
         │
         ▼
  URL Router (urls.py)
         │
         ▼
  View Function (views.py)
  ├─ Get queryset from database
  ├─ Apply filters (status, category, date)
  ├─ Calculate aggregations
  └─ Render template with context
         │
         ▼
  Template Rendering
  ├─ Load template file (expenses/list.html)
  ├─ Loop through expenses
  ├─ Display filters
  └─ Generate HTML
         │
         ▼
  HTTP Response (200 OK)
  └─ Content-Type: text/html
     Body: Rendered HTML page
```

## Component Dependency Graph

```
View Layer
    │
    ├─ expense_list
    ├─ expense_detail
    ├─ expense_create
    ├─ expense_edit
    ├─ expense_delete
    ├─ expense_approve
    ├─ expense_report
    └─ expense_dashboard (NEW)
         │
         ▼
Form Layer (ExpenseForm)
         │
         ▼
Model Layer
    │
    ├─ Expense
    │  ├─ Category (FK)
    │  ├─ Project (FK, optional)
    │  ├─ Client (FK, optional)
    │  └─ User (ForeignKey)
    │
    └─ ExpenseCategory
         │
         ▼
Template Layer
    ├─ expenses/list.html
    ├─ expenses/form.html
    ├─ expenses/detail.html (NEW)
    ├─ expenses/report.html (NEW)
    └─ expenses/approve.html
         │
         ▼
Admin Layer
    ├─ ExpenseCategoryAdmin
    └─ ExpenseAdmin
```

## Workflow State Machine

### Expense Status Flow
```
         ┌─────────────┐
         │   Created   │
         │  (pending)  │
         └──────┬──────┘
                │
         ┌──────┴──────┐
         ▼             ▼
    ┌────────┐    ┌────────┐
    │Approved│    │Rejected│
    └────┬───┘    └────────┘
         │
         ▼
    ┌──────────────┐
    │ Reimbursed   │
    │  (final)     │
    └──────────────┘
```

### Invoice Reminder Status Flow
```
┌────────────────┐
│ Invoice Issued │
│ reminder_sent= │
│    False       │
└────────┬───────┘
         │
    30 days pass
         │
         ▼
┌────────────────────┐
│ Reminder Sent (1st)│
│ reminder_sent=True │
│ last_reminder_date │
│ = current datetime │
└────────┬───────────┘
         │
    30 days pass
         │
         ▼
┌────────────────────┐
│ Reminder Sent (2nd)│
│ reminder_sent_count│
│ = 2                │
└────────┬───────────┘
         │
    30 days pass
         │
         ▼
┌────────────────────┐
│ Reminder Sent (3rd)│
│ reminder_sent_count│
│ = 3                │
└────────────────────┘
```

## Technology Stack

```
┌─────────────────────────────────────────┐
│         TECHNOLOGY STACK                │
├─────────────────────────────────────────┤
│                                         │
│ Backend:                                │
│ ├─ Python 3.8+                         │
│ ├─ Django 4.2+                         │
│ ├─ Django ORM                          │
│ └─ Django Admin                        │
│                                         │
│ Database:                               │
│ ├─ PostgreSQL (Recommended)             │
│ ├─ MySQL                               │
│ └─ SQLite (Development)                │
│                                         │
│ Frontend:                               │
│ ├─ HTML5                               │
│ ├─ CSS3 (Bootstrap 5)                  │
│ ├─ JavaScript                          │
│ └─ Jinja2 Templates                    │
│                                         │
│ Email:                                  │
│ ├─ SMTP Server                         │
│ ├─ django.core.mail                    │
│ └─ Template rendering (Jinja2)         │
│                                         │
│ Scheduling:                             │
│ ├─ Cron (Linux/Mac)                    │
│ ├─ APScheduler (Optional)              │
│ └─ Celery Beat (Optional)              │
│                                         │
│ Deployment:                             │
│ ├─ Gunicorn/uWSGI                      │
│ ├─ Nginx/Apache                        │
│ └─ Heroku/VPS                          │
│                                         │
└─────────────────────────────────────────┘
```

---

**Architecture Version**: 1.0
**Created**: 2024
**Status**: Phase 1 Complete
