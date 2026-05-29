# CRM Reports Feature Plan
## Provost Home Design - Portal Enhancement

### Overview
Build a comprehensive reporting and analytics system within the portal to filter, review, and export business data (invoices, expenses, clients, projects, etc.).

---

## Phase 1: Reports Infrastructure

### 1.1 Database Models (if not already present)
```
apps/crm/models.py

- Client (name, email, phone, address, created_at, updated_at)
- Project (client_fk, name, description, status, start_date, end_date)
- Invoice (project_fk, number, amount, status, issued_date, due_date, paid_date)
- Expense (project_fk, description, amount, category, date, receipt_url)
- Transaction (invoice_fk, type, amount, date, method, notes)
```

### 1.2 Reports Base Views
```
apps/crm/views.py - ReportsMixin

class ReportsMixin:
  - get_date_range() - Parse start/end dates from query params
  - get_filters() - Extract filter criteria (status, category, etc.)
  - apply_filters() - Build QuerySet with filters
  - aggregate_data() - Sum totals, counts, averages
  - format_for_export() - CSV/Excel serialization
```

---

## Phase 2: Core Report Types

### 2.1 Invoice Reports
**Route:** `/portal/reports/invoices/`

**Filters:**
- Date range (issued, due, paid)
- Status (draft, sent, paid, overdue, cancelled)
- Client
- Amount range
- Project

**Metrics Displayed:**
- Total invoices count
- Total revenue (all time, by period)
- Average invoice value
- Overdue total amount + count
- Paid vs unpaid breakdown
- Revenue by client
- Revenue by project
- Revenue by month (chart)

**Columns in Table:**
- Invoice #
- Client
- Project
- Amount
- Status
- Issued Date
- Due Date
- Paid Date
- Actions (view, edit, mark paid)

**Export Options:**
- CSV
- Excel (with formatting)
- PDF (with company header/footer)

---

### 2.2 Expense Reports
**Route:** `/portal/reports/expenses/`

**Filters:**
- Date range
- Category (labor, materials, equipment, permits, subcontractors, other)
- Project
- Amount range
- Status (pending, approved, rejected)

**Metrics Displayed:**
- Total expenses
- Total spent
- Average expense
- Expenses by category (pie chart)
- Expenses by project
- Expenses by month (chart)
- Budget vs actual (if budgets tracked)

**Columns in Table:**
- Date
- Project
- Category
- Description
- Amount
- Status
- Receipt (link/preview)
- Actions (view, approve, reject, edit)

---

### 2.3 Project Reports
**Route:** `/portal/reports/projects/`

**Filters:**
- Status (active, completed, on-hold, cancelled)
- Date range (start, end)
- Client
- Revenue range
- Profitability (yes/no)

**Metrics Displayed:**
- Active projects count
- Completed projects count
- Total project revenue
- Total project expenses
- Overall profitability
- Average project profit margin %
- Projects by status (bar chart)
- Revenue by project (table)
- Profitability by project (table: revenue - expenses)

**Columns in Table:**
- Project Name
- Client
- Status
- Start Date
- End Date
- Revenue
- Expenses
- Profit
- Margin %
- Actions (view details, edit)

---

### 2.4 Client Reports
**Route:** `/portal/reports/clients/`

**Filters:**
- Status (active, inactive, prospect)
- Total revenue range
- Date range (first invoice, last invoice)
- Location/region (if tracked)

**Metrics Displayed:**
- Total clients count
- Active clients count
- Total lifetime revenue
- Average revenue per client
- Top 10 clients by revenue
- Clients by status (pie chart)
- Client lifetime value (table)

**Columns in Table:**
- Client Name
- Total Revenue
- # of Projects
- # of Invoices
- Last Project Date
- Status
- Actions (view profile, all invoices)

---

### 2.5 Dashboard Summary
**Route:** `/portal/reports/`

**Quick Stats (Cards):**
- YTD Revenue (vs last year)
- Outstanding Invoices (amount + count)
- Pending Expenses (count + amount)
- Active Projects (count)
- New Clients (this month)

**Charts:**
- Revenue trend (last 12 months)
- Profitability trend (last 12 months)
- Invoice status breakdown (pie)
- Expenses by category (pie)
- Top 5 projects by revenue (bar)

**Recent Activity:**
- Last 5 invoices
- Last 5 expenses
- Last 5 projects started

---

## Phase 3: Advanced Filtering & UI

### 3.1 Filter Components
```html
<FilterPanel>
  - DateRangeFilter (start_date, end_date)
  - MultiSelectFilter (checkboxes for status, category, etc.)
  - MoneyRangeFilter (min_amount, max_amount)
  - SearchFilter (text search)
  - SavedFilterButton (save/load filter presets)
  - ClearFiltersButton
</FilterPanel>
```

### 3.2 Reports Layout
```
/portal/reports/
├── Navigation Sidebar
│   ├── Dashboard
│   ├── Invoices
│   ├── Expenses
│   ├── Projects
│   ├── Clients
│   ├── Saved Reports
│   └── Settings
├── Filter Panel (collapsible)
├── Report Content
│   ├── Metrics Cards
│   ├── Charts (Chart.js or similar)
│   ├── Data Table (sortable, paginated)
│   └── Export Buttons
└── Footer (summary stats)
```

### 3.3 Data Table Features
- Sortable columns
- Pagination (25/50/100 rows per page)
- Column visibility toggle
- Inline row actions (edit, delete, approve, etc.)
- Bulk actions (select multiple, mark status, delete)
- Search/filter within table

---

## Phase 4: Export & Sharing

### 4.1 Export Formats
- **CSV** - Simple spreadsheet, universal
- **Excel** - .xlsx with formatting, charts, multiple sheets
- **PDF** - Professional report with branding, charts embedded

### 4.2 Scheduled Reports
- Email daily/weekly/monthly summaries
- Auto-generate and archive reports
- Send to stakeholders

### 4.3 Saved Reports
- Save filter combinations as "Smart Reports"
- One-click run saved report
- Share report link (read-only)

---

## Phase 5: Implementation Steps

### Step 1: Create CRM App Structure
```bash
python manage.py startapp crm
```

**models.py** - Define Client, Project, Invoice, Expense, Transaction
**views.py** - Create report view classes
**forms.py** - Create filter forms
**urls.py** - Route all report endpoints
**utils.py** - Helper functions for aggregation, export

### Step 2: Build Report Views
```python
# apps/crm/views.py

class ReportsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "crm/reports/dashboard.html"
    
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['ytd_revenue'] = Invoice.objects.filter(...).aggregate(...)
        ctx['invoices_pending'] = Invoice.objects.filter(status='sent')
        ctx['chart_revenue_12mo'] = get_revenue_last_12_months()
        return ctx

class InvoiceReportView(LoginRequiredMixin, ListView):
    template_name = "crm/reports/invoices.html"
    
    def get_queryset(self):
        qs = Invoice.objects.all()
        # Apply filters from GET params
        qs = self.apply_filters(qs)
        return qs
    
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['total_revenue'] = sum([obj.amount for obj in self.get_queryset()])
        ctx['overdue_total'] = self.get_queryset().filter(status='overdue').aggregate(Sum('amount'))
        return ctx
```

### Step 3: Create Templates
```
templates/crm/
├── base.html (extends portal base)
├── reports/
│   ├── dashboard.html
│   ├── invoices.html
│   ├── expenses.html
│   ├── projects.html
│   ├── clients.html
│   ├── _filter_panel.html (reusable)
│   ├── _metrics_cards.html (reusable)
│   └── _data_table.html (reusable)
```

### Step 4: Add Charting Library
```html
<!-- Include in base template -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>

<!-- or use -->
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
```

### Step 5: Export Functionality
```python
# apps/crm/utils.py

def export_to_csv(queryset, filename):
    import csv
    from django.http import HttpResponse
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    writer = csv.writer(response)
    writer.writerow(['Header1', 'Header2', ...])
    for obj in queryset:
        writer.writerow([obj.field1, obj.field2, ...])
    
    return response

def export_to_excel(queryset, filename):
    import openpyxl
    # Similar implementation with styling, formulas, charts
    
def export_to_pdf(queryset, filename):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, Paragraph, Spacer
    # Build professional PDF with branding
```

### Step 6: Add Permissions/Access Control
```python
# In views
class ReportsDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = 'crm.view_reports'
    
# In admin.py
class ReportPermissionGroup(PermissionGroup):
    permissions = ['view_reports', 'export_reports', 'manage_filters']
```

---

## Phase 6: Performance Optimization

### Database Optimization
- Add indexes on date fields, status, client_fk
- Use `select_related()` and `prefetch_related()` in queries
- Cache aggregation results (Redis)

### Caching Strategy
```python
# Cache expensive queries
from django.core.cache import cache

def get_revenue_last_12_months():
    cache_key = 'revenue_12mo'
    data = cache.get(cache_key)
    if not data:
        data = Invoice.objects.filter(...).aggregate(...)
        cache.set(cache_key, data, 3600)  # 1 hour
    return data
```

### Pagination
- Start with 25 rows per page
- Lazy load charts if many data points
- Use AJAX for filter updates

---

## Desktop Application Integration

### Architecture Options:

#### Option 1: Electron + Web Wrapper (EASIEST - 7/10)
```
pros:
- Reuse 100% of web code
- Works offline (with sync)
- Cross-platform (Windows, Mac, Linux)
- Easy to maintain - update web = update app
- Can access local file system for faster exports

cons:
- Slightly heavier app (~150MB)
- Performance not as native

implementation:
1. Build Electron wrapper around your Django API
2. Host Django as backend service (local or cloud)
3. Electron UI communicates via HTTP/WebSocket
4. Add offline mode with local SQLite sync
```

#### Option 2: Tauri (MEDIUM - 8/10)
```
pros:
- Lighter than Electron (~20MB)
- Better performance
- Native feel
- Rust backend for performance

cons:
- Newer, less documentation
- Smaller community

implementation:
- Similar to Electron but with Rust backend
- Frontend can still be your Django templates or React
```

#### Option 3: Native Python App (HARD - 6/10)
```
Using PyQt, Tkinter, or wxPython
pros:
- Native Python
- Direct database access
- Small app size

cons:
- Can't reuse web frontend (duplicate code)
- More work to maintain two codebases
- Platform-specific UI differences

implementation:
- Build PyQt/wxPython UI
- Call Django API or connect directly to DB
- Significant code duplication
```

#### Option 4: Progressive Web App + "Install" (MEDIUM - 7/10)
```
pros:
- Single codebase (your Django app)
- Works on any device
- No installation needed
- Automatic updates

cons:
- Still web-based
- Offline limitations

implementation:
- Add PWA manifest
- Service workers for offline
- Install prompt in browser
```

---

## Recommendation for Desktop App

**Start with Electron Wrapper (Option 1):**

1. **Immediate Benefits:**
   - Reuse 100% of portal code
   - Ship desktop app in 2-3 weeks
   - Update once = updates both web and desktop

2. **Implementation:**
```javascript
// main.js (Electron)
const { app, BrowserWindow } = require('electron');

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: { nodeIntegration: false }
  });
  
  // Point to local Django server
  win.loadURL('http://localhost:8000/portal/reports/');
  
  // OR package Django in app and run it
  // startDjangoServer();
}

app.whenReady().then(createWindow);
```

3. **Offline Capability (Phase 2):**
   - Service Worker caches templates/assets
   - Local SQLite for data sync
   - Sync button to pull latest from server

4. **File Access:**
   - Export to user's Documents folder
   - Open files with native apps
   - PDF viewer integration

5. **Deployment:**
   - Build installers for Windows (.exe), Mac (.dmg), Linux (.AppImage)
   - Auto-update mechanism
   - ~5-10MB for just UI wrapper, +Django backend size

---

## Timeline & Effort Estimate

| Phase | Task | Duration | Difficulty |
|-------|------|----------|-----------|
| 1 | Reports Infrastructure & Models | 1 week | Medium |
| 2 | Dashboard + 5 Core Reports | 2 weeks | Medium |
| 3 | Advanced Filtering & UI | 1.5 weeks | Medium |
| 4 | Export (CSV/Excel/PDF) | 1 week | Medium |
| 5 | Permissions & Access Control | 3 days | Easy |
| 6 | Performance & Caching | 1 week | Hard |
| **Total (Web Portal)** | | **6-7 weeks** | |
| **Bonus: Desktop App (Electron)** | | **2-3 weeks** | Easy |

---

## Next Steps

1. ✅ Finalize data models (Client, Project, Invoice, Expense)
2. ✅ Create CRM app structure
3. ✅ Build dashboard view with KPIs
4. ✅ Implement first report (Invoices)
5. ✅ Add filtering & UI polish
6. ✅ Export functionality
7. ✅ Electron wrapper for desktop
8. ✅ Deploy & test

---

## Questions?

- Should expenses be tracked per project or globally?
- Do you need multi-user support with role-based access?
- Should reports be real-time or cached for performance?
- Do you want mobile-first reporting dashboard?
