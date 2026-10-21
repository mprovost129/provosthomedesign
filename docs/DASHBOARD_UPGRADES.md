# Dashboard filing, billing overview, and shortcuts

## Staff use

The dashboard links to a paginated **Project filing** inbox at
`/dashboard/filing/`. Choose an approval/Dropbox/FreshBooks status, or search by
request, project number, project name, address, or client. Each result opens the
existing filing preview and approval screen. No filing is approved from a GET.

Counts are queue requests, not files. Multiple submissions on one request count
once per status. Statuses can overlap. Unfiled submissions with privately stored
files remain visible even after the work is completed. Active requests without
files can still be approved to create/connect a project folder. Closed legacy
requests without stored uploads do not flood the approval inbox. Deleted requests
are excluded. FreshBooks project progress appears on the request which approved
it, rather than on every update of the connected project.

**Add work** opens the queue's manual-entry form. **Add client** uses the existing
CRM form. **Find a project** searches all project workspaces, including completed
work. Existing staff/add/change permissions and preview restrictions apply.

## Billing summaries

`DashboardBillingSummary` stores complete checked aggregates separately from
incomplete scan state. It is keyed by account, kind and period. Invoice scans
cover the selected FreshBooks business directly; imported and connected copies
are never combined, so no duplicate accounting occurs. Outstanding excludes
drafts, created, voided, deleted, declined and pending invoices. Archived
receivables remain included. Overdue is a subset of outstanding, using the
provider overdue status or a due date before the check's Eastern date.

Monthly payments use actual invoice payment dates from month-start through the
check date. They exclude deleted payments, credits/credit transfers and other
income, and include negative payment entries in the net total. These are recorded
invoice payments, not profit, card settlement proceeds or bank deposits. Each
currency remains separate. A legitimate zero is shown only after a completed
empty scan; missing or failed checks never become zero.

FreshBooks payment records require **user:payments:read**. The connection page
offers an explicit optional authorization flow. It adds no payment-write scope;
ordinary billing authorization retains its previous scope set. Reconnects
preserve an already granted payment-read scope. Missing payment access does not
block invoice balance checks, project filing, or existing billing functionality.

The existing scheduled `sync_freshbooks_billing` command enqueues due summaries
and processes one provider page per run. No new cron service is required. Checks
refresh daily, use a five-minute manual throttle, and back off automatic failures
for 30 minutes. POST `/dashboard/billing/check/` only queues a check and requires
CSRF and change permission. Dashboard and filing GETs perform no provider calls.

Each scan uses a short durable lease, bounded pagination (100 rows/page, maximum
100 pages), total/page checks, duplicate provider-ID checks, strict account,
status, currency, decimal and date validation. Partial or failed scans never
replace checked totals. A changed business prevents publication. Month rollover
does not reuse the previous month's payment totals. Expired read leases may be
reclaimed safely because all provider requests in this module are GETs. Tokens,
payment notes, customer data and full financial records are not stored here.

## Deployment

Apply additive migration `0043_dashboard_billing_summary`. Deploy the web service
and existing queue cron from the same commit. No new environment variables,
storage, hosting charges, invoices, payments, emails or test project numbers are
required. Only the optional FreshBooks payment-read grant requires owner consent.

## Validation

`workqueue.test_dashboard_upgrades` covers permission and CSRF gates, shortcuts,
filing counts/filters and no-write GETs, scan throttles, currency separation,
due-date boundaries, payment dates/credits/negative entries, explicit zero versus
missing data, partial scans, duplicate IDs, malformed money/status/pagination,
provider failures, account changes, lease recovery, month rollover and the
optional read-only OAuth scope. Run with PostgreSQL like production, alongside
the existing dashboard, FreshBooks and scheduled-job tests.

Provider references: [Invoices](https://www.freshbooks.com/api/invoices),
[Payments](https://www.freshbooks.com/api/payments),
[Archived records](https://www.freshbooks.com/api/active_deleted).
