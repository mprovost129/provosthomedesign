# Appointment booking — stage 3, October 7, 2026

The Django intake app now has verified client booking, rescheduling and cancellation, Google Calendar synchronization, appointment notifications and controls on the existing work queue page. The owner selected the **primary Google Calendar**. Production booking stays off by default; this stage changes no live Google events, booking-page settings, Render configuration or website links.

## Agreed rules implemented

| Rule | Behavior |
| --- | --- |
| Meeting | 30 minutes |
| Buffer | 30 minutes immediately afterward, blocking the entire hour |
| Days | Tuesday–Friday |
| Window | 10 AM–2 PM America/New_York, accounting for daylight saving changes |
| Start times | Every 30 minutes from 10 AM through 1 PM; 1:30 PM is rejected |
| Notice | At least 24 hours measured between actual instants |
| Daily capacity | Two appointments total across new/existing clients |
| Client capacity | One upcoming appointment per verified email |
| Purpose | Required: What would you like to discuss? |
| Calendar conflicts | Busy events, recurring instances, all-day events and meeting buffers block overlapping times |
| Booking horizon | 60 days ahead; no promise of a design start date |

Closed/canceled reservations release future capacity only when calendar deletion is known. Unknown or partial calendar writes continue to hold their reserved intervals until reconciled. A reschedule holds the original time until the replacement is saved and the old calendar entries are removed; conservative temporary holds prevent other clients claiming either interval while that change is in progress.

## Client flow

- `/book-appointment/` is linked from intake, submission confirmation and tracking. It does not create another work item or change any queue order/priority.
- Anyone can request a passwordless sign-in link. The response does not disclose whether an email has booking permission. The confirmed link returns directly to booking.
- New customers gain booking permission after submitting new work and verifying the original intake email. Existing clients can receive permission once from the queue page without another submission. Re-verification never restores an explicitly revoked booking grant.
- Required booking questions: Full name; Phone number; Available start time (Eastern); What would you like to discuss? Email is taken from verified access. Related project is optional and limited to the client's authorized projects. Name/phone can be prefilled from the client's own captured submission, not another project participant's contact record.
- The client sees Pending while calendar synchronization is unresolved, then Confirmed after both meeting and buffer events are verified. Each booking/reschedule/cancellation confirmation queues one client email and one owner email. Real delivery uses the previously implemented durable outbox worker.
- Clients can reschedule or cancel their own future appointment. Existing reservations remain cancellable after booking access is revoked. A brief calendar update in progress must finish before cancellation, preventing an old time from being lost halfway through a reschedule.
- Meeting location/call details are not invented. Confirmation says Mike will coordinate meeting details; automatic Google Meet generation is not included.

## Owner management on one page

`/work-queue/#appointments` lists pending/upcoming reservations, client contact/purpose/project, calendar exceptions and notification delivery. It provides booking permission by email, cancellation requests, calendar reconciliation retries, and explicit failed/unknown email retry controls. A collapsed section retains the most recent 25 closed appointments and their email exceptions. Work queue counts and order remain independent of appointments.

Permissions: the existing protected staff queue still requires `view_workitem`; changing booking eligibility additionally requires `change_bookingaccess`, and appointment/email actions require `change_appointment`. All booking/access changes produce `BookingAudit` history. Client access uses verified email plus object-level checks and normal CSRF protection. Public sequential work IDs do not grant booking or project access.

## Calendar synchronization and failure handling

Production uses the Google Calendar REST API through the existing `requests` dependency. No new dependency is required. `primary` is resolved to a concrete calendar ID before reservation, and that ID is saved so refreshing credentials for a different account cannot redirect an old reservation to another primary calendar. Availability uses expanded event instances, ignores transparent/canceled events, follows pagination and interprets all-day dates in the calendar's own time zone. Calendar details unrelated to the booking are never rendered to clients.

Each appointment has two stable Google event IDs derived from its UUID: a private opaque 30-minute meeting and a separate private opaque 30-minute buffer. The meeting description contains the client's name, email, phone and purpose; the buffer contains no client details. Google attendees are deliberately not added, avoiding duplicate Google invitation emails or advertising a 60-minute client meeting. Confirmation emails are sent by Django. Calendar invitations/ICS attachments can be a subsequent explicit enhancement.

A seeded `BookingState` row serializes local reservations, daily limits and client limits on PostgreSQL. Calendar calls happen outside the reservation database lock. The worker checks busy intervals before and after writes, verifies event IDs/ownership/times, and confirms only after both events are known. Retries reconcile existing IDs rather than allocating new Google events. DELETE uses the event ETag and confirms the absence afterward; mismatched ownership is never deleted. An expired worker claim cannot overwrite a newer reconciliation outcome.

The worker also checks confirmed bookings periodically. Events moved/deleted directly in Google are flagged for owner review instead of silently recreated or adopted. Calendar outages, partial writes and ambiguous responses preserve reservations and produce a generic owner-visible exception. Unconfirmed bookings whose time passes remain exceptions for review, rather than being marked successful. Successful bookings become Completed after the meeting/buffer interval. Cancellation requests and new reservations take priority over routine rechecks.

Google cannot provide a transaction combining availability checking and event insertion. Another calendar user can add a conflicting event immediately after a check. The app checks again and periodic reconciliation flags later conflicts, but it cannot promise atomic exclusion against independent Google editors. App-side reservations are serialized. The previous public Google booking page must be retired/restricted at cutover to prevent bypassing client access/daily limits.

Reference documentation: [Google event listing](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [event insertion](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), [event deletion](https://developers.google.com/workspace/calendar/api/v3/reference/events/delete), [calendar metadata](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/get), and [OAuth for web applications](https://developers.google.com/identity/protocols/oauth2/web-server).

## Production connection and deployment

The local project had no Google Calendar configuration keys when inspected. Live credentials/authorization and deployed Render settings were not accessed or changed. Keep this feature off until the owner connects the primary calendar and live integration is verified:

```text
BOOKING_ENABLED=False
GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_CLIENT_ID=<OAuth client ID>
GOOGLE_CALENDAR_CLIENT_SECRET=<OAuth client secret>
GOOGLE_CALENDAR_REFRESH_TOKEN=<owner-authorized refresh token>
```

Use a Google Cloud OAuth application with Calendar API enabled. Required scopes are `https://www.googleapis.com/auth/calendar.events.owned` (read/write owned primary-calendar events) and `https://www.googleapis.com/auth/calendar.calendars.readonly` (resolve calendar identity/time zone). The owner must complete Google's consent flow. Store the refresh token and client secret in Render's secret environment, never in the repository, database outbox, chat or logs. Access tokens are kept only in the provider object's process memory; errors do not include provider responses or token values. Resolve Google's OAuth publishing/test-user requirements before production; a temporary testing authorization is not a durable launch configuration.

Migrations 0006–0009 add booking records, seed the allocator/grants for already-verified new-intake claims, add booking link destinations and worker/data guards. These migrations do not import existing Google bookings, create appointments or send emails. Apply migrations through the normal deployment process, preserving all earlier queue/import requirements.

Read-only connection check (creates no events/emails):

```text
python manage.py check_queue_calendar
```

After configuration, schema deployment, imported queue verification and live integration testing, enable `WORK_QUEUE_ENABLED`, `WORK_INTAKE_ENABLED` and `BOOKING_ENABLED`. Configure jobs with the same database, Google credentials, storage and email environment:

```text
python manage.py sync_queue_appointments --limit 50
python manage.py process_queue_emails --limit 100
python manage.py cleanup_queue_uploads
```

Run calendar/email jobs frequently, such as every minute, and upload cleanup hourly. Pending bookings/cancellations are immediate candidates; routine calendar checks retry after five minutes and stale worker claims after fifteen minutes. Monitor Attention appointments, pending/failed/unknown notifications and worker health. The read-only check proves identity/availability, not write/delete permissions; a controlled live meeting+buffer save/cancel is still required before launch.

Before cutover, identify and preserve existing client appointments made through the old Google booking page. Import their ownership, scheduled intervals and daily capacity reservations or block the affected days explicitly; ordinary busy events alone cannot identify which external meetings should count toward the two-client-appointments daily limit. Retire/restrict the old booking route, migrate the Sheet with reference/order reconciliation, verify real S3 and outbound email behavior, and then change public website links. Rollback must account for appointments and submissions created after cutover.

## Local preview and validation

`config.settings_queue_dev` enables a clearly labeled fictitious calendar, isolated SQLite data and in-memory mail. The test calendar cannot run through the normal production settings. The preview creates no real Google events and sends no external emails. Preview confirms appointments immediately so browser interaction can be reviewed; production uses the durable synchronization job.

The browser walkthrough booked, rescheduled and canceled a fictitious client appointment; cancellation restored available times. The same appointment was visible from the owner queue. Automated checks cover day/hour/buffer/notice limits, daylight saving changes, daily/client capacity, access/privacy/CSRF, idempotency, partial writes, cancellation/reschedule failures, provider identity, all-day events, stale workers and notification timing. A related private-upload cleanup fix preserves distinct metadata keys when removing several expired files.

Final regression result: **191 tests discovered, 187 passed, four PostgreSQL-only concurrency tests skipped** on SQLite. Django system and migration consistency checks passed. The two new PostgreSQL checks cover parallel same-slot reservations and the daily limit; the existing two cover queue allocation/retries. Run all four against the dedicated local PostgreSQL test environment described in the foundation document before production enablement. Actual Google authorization/event writes, Render jobs, real private S3 and external email delivery remain unverified.

## Calendar connection completed — October 7, 2026

The Google Calendar API is enabled in the existing Provost Home Design Cloud project (`possible-arch-501217-r3`). The dedicated web OAuth client is named **Provost Home Design Queue Calendar**, with the private one-time authorization return address `http://127.0.0.1:8766/oauth/callback`. Google authorization remains **Internal** to the business organization. Mike approved the two required Calendar permissions for `mike@provosthomedesign.com`; the app uses that account's primary calendar. The authorization code was exchanged directly with Google's token endpoint by a loopback-only local connection tool, with state and PKCE validation.

The four `GOOGLE_CALENDAR_*` settings listed above are saved in the **provosthomedesign** Render web service (`srv-d8cps8urnols739r24sg`) using **Save only**. Its website is `https://web.provosthomedesign.com`, deployed from `mprovost129/provosthomedesign` on `main`. The existing 40 environment settings were preserved. A fresh Render page load confirmed all 44 keys. Credential values are not recorded here or in GitHub.

Read-only verification passed for calendar ownership, availability and automatic refresh-token renewal, including the website's existing Google Calendar adapter. Verification used the local Windows runtime's trusted certificate authorities with TLS checks enabled. No Google events or external emails were created, changed or deleted.

This connection step did **not** push repository changes, deploy/restart the Render website, apply production migrations, enable live booking, create background jobs, or change the old public Google booking page. The running preview still uses its fictitious calendar. Before launch, deploy the new app, configure and verify calendar/email workers, test a controlled real meeting plus buffer and cancellation, and complete the queue/existing-appointment reconciliation and public-link cutover described above. Keep the Google credentials configured on every process that will synchronize appointments.
