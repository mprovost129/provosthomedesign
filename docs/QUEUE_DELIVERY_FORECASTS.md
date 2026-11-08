# Design delivery forecasts

Queue forecasts now describe the next deliverable, not a whole-project deadline.

## Defaults and daily use

- Each new request/update defaults to **8 remaining design hours** and **Next drawing update**. Adjust these in Work Queue > Manage > Delivery forecast and client-visible update. Blank hours reset to 8. Blank deliverable resets to the default label.
- In Work Queue > Design capacity, the initial schedule is **Monday-Friday, 8:30 a.m.-3:30 p.m. Eastern**, with **5 actual design hours per day**. The other two hours are reserved for meetings, calls and other work. Capacity and times are editable.
- The estimate includes remaining work ahead in the **entire saved queue**, even while searching, filtering or paginating. Priority labels do not silently reorder work: use Arrange queue to move urgent work ahead.
- Needs Information and On Hold retain queue positions but consume no design capacity. Closed/deleted requests have no delivery estimate. Resuming or reordering work recalculates affected windows.
- A scheduled start prevents allocating time before that date. Other work can fill unreserved earlier days.
- The start of the delivery window is the calculated finish day. Its end adds **2 working days** by default, editable from 0 to 10. Weekends and explicitly entered days off/holidays are skipped for both calculation and allowance.
- Today's capacity is prorated across the workday. After 3:30 p.m., allocation begins on the next working day. Forecasts use America/New_York, including daylight-saving time.
- Remaining hours are **manual**, never reduced merely by time passing or automatically deducted from billing/time entries. Update them when progress is made. Appointment meetings are not double-deducted from the 5-hour allowance.

## Client visibility and privacy

Track My Project and the original-browser submission confirmation show the estimated window, next deliverable and a separate **What we need from the client** field. Those two text fields are explicitly client-visible; internal notes remain private. Waiting/held work shows an explanatory status instead of a misleading date. Positions remain secondary context. No other client's details or internal effort estimates are exposed. Existing authorization checks still apply.

Windows are estimates, not promised due dates or calendar bookings. Existing committed due dates remain unchanged and visible to staff. Saving estimates, capacity or client action text sends **no emails**. Receipt and milestone-email behavior is unchanged.

## Data and deployment

Migration 0064 is additive. Existing requests without an estimate receive 8 hours. Explicit legacy estimated_days values are converted at 5 design hours/day while the original values remain stored; the pre-cutover legacy importer follows the same rule. The default schedule is seeded once and subsequent deploys do not reset edited capacity. New WorkItem columns have database defaults for compatibility with older app workers during rollout.

Schedule edits require existing view/change-work permissions and use the queue lock plus version conflict checking. Request estimate edits use existing audited/versioned edits. No new service, external integration, recurring job or setting is required. Render applies the migration through its existing build command. Roll back code rather than reversing/dropping the additive migration or resetting client data.

## Validation

Focused tests cover default and fractional hours, whole-queue accumulation, queue priority/reordering, future starts, Eastern/DST boundaries, partial days, weekends/days off, blocked/closed/deleted work, large estimates, edit conflicts, permissions, migration conversion, and client isolation/escaping. Existing queue/intake/privacy tests and the full repository PostgreSQL/JavaScript checks are run before release.
