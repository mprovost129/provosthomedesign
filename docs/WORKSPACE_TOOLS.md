# Shared search, workload and phone queue

All three tools use existing staff sign-in, queue-view permissions and the optional enrolled authenticator. No public client lookup access is added.

## Shared search

The staff navigation and dashboard quick action open `/staff/search/`. Search saved clients, active contacts, project names/addresses, PHD request IDs, six-digit filing numbers, proposal numbers/titles and invoice numbers. Words are matched together across the category's fields. Queries need two characters, are limited to 120 characters and six terms. Each category initially shows six results; “View all” opens pages of 30. Active and closed requests are searchable; deleted requests, merged clients and archived contact email matches are excluded.

Imported invoice history is suppressed where a connected invoice has the same account and normalized number or known provider invoice ID. Different billing accounts are never merged by invoice number alone. Search performs no provider refresh or write, and never indexes passwords, private notes, proposal drafts, billing payloads or document contents. Results are private and non-cacheable.

## Weekly workload

`/work-projects/workload/` shows four Monday–Sunday weeks, earlier targets, later targets and projects without dates. It uses one row per project with visible active work. The earliest active request committed due date and project plan target date determine the target; the earliest scheduled start is the fallback. Closed/deleted requests do not determine targets. Open tasks due in the four-week window add counts without adding hours.

Known remaining hours are the project plan's estimated hours minus all recorded, completed time entries on the project, floored at zero. Nonbillable time still consumes the project budget. Deleted time entries and running timers are excluded. Missing estimates remain explicitly unknown; zero estimates and overruns are distinct. Linked updates never multiply project budgets. Up to 30 project cards per group are shown; totals include any additional cards. Active requests without a project link are called out separately.

These are project budgets grouped by target week, not a capacity calendar or an automatic week-by-week allocation. No promised completion date is inferred, and public queue positions do not change. Edit project estimates/targets in the project workspace and request due/start dates in the queue.

## Phone queue

At widths up to 700 pixels, the existing queue table becomes cards. It retains the same IDs, status forms, CSRF/version fields, file links, email editor, filing links, edit history and unsaved-edit handling. No second set of input forms is rendered. Row actions start collapsed on phones and open on desktop; changing layouts does not clone forms or change their data. Status saves and milestone-email choices use the existing handlers.

No migration, external integration, hosting setting or recurring charge is added by this update.
