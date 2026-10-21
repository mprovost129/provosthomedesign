# Provost Home Design staff dashboard

Open `/dashboard/` while signed in as staff with the existing `view_workitem`
permission. Dashboard links appear in staff navigation, the queue, client list,
client records, and the staff-only website menu/footer.

The page is a read-only starting point for the existing tools:

- Active request, active project, current CRM client, and awaiting-proposal counts.
- Queue attention categories, due CRM follow-ups and project tasks, proposal
  changes, connected invoices requiring review, and imported invoices with saved
  balances due. Imported balances use the existing history filter and may require
  a refresh; they are not claimed to be current debts. Counts overlap and are not
  added into a misleading combined total.
- Active requests grouped by status, linking to the existing queue filters.
- Next five website appointments, including meetings currently underway. Canceled
  appointments and appointments awaiting cancellation are omitted. These are
  saved website bookings, not a live view of all Google Calendar events.
- Six recent requests, projects and clients; five recent proposals. Project
  recency follows work/planning changes. Client recency follows profile/work
  changes. Deleted work and merged-away CRM profiles are excluded.
- Six recent invoice records from connected billing and visible imported history.
  A same-account provider invoice ID or normalized invoice number is shown once,
  preferring the connected record. Connected creation date and historical issue
  date are separately labeled. Full invoice payloads and imported line JSON are
  deferred from these lists.
- Last saved invoice status/check time and the existing background job health.

The dashboard never sends email, provisions folders/projects, changes queue
order/status, starts sync jobs, or calls a provider API. Refresh reloads saved
data only. Use the existing invoice tools to refresh provider balances. It does
not present imported invoice totals as income or mix currencies into a total.

Authentication, queue feature gating and no-store/noindex protections reuse the
existing staff view decorator. Public visitors and client email verification
sessions do not grant access. Existing tools, public intake and login destinations
remain available at their current URLs. No database migration or new credentials
are required.

Validation: `workqueue.test_dashboard` covers access controls, GET-only/no-write
behavior, counting rules, due filters, appointment visibility, invoice duplicate
handling, saved-status labels, empty states, escaping, bounded query counts and
staff-only navigation. Test on PostgreSQL as used in production.
