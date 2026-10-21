# Operational error alerts

The existing independent Google Apps Script website/queue monitor remains
unchanged. It covers prolonged outages, stale background jobs and failed email.
This release adds individual server-error capture and owner email summaries.

Unexpected view exceptions and HTTP 5xx responses are grouped by resolved route
name, HTTP method, exception class and code location. Records exclude raw URLs,
query strings, headers, cookies, IPs, user identities, form fields, file contents,
exception messages, local variables and full stack traces. Expected 400/403/404
responses do not create incidents. One failed request is counted once even when
the error handler and middleware both see it. Concurrent occurrences are counted
atomically. Background job exceptions also record their command name.

Public 500 pages and all 5xx responses expose an opaque ERR reference. The normal
500 page falls back to minimal HTML when its template, context or database fails.
If incident storage itself fails, the response still carries a reference and a
sanitized fallback log entry is written. A total database outage cannot persist
an incident; the independent monitor remains the fallback.

`/dashboard/errors/` uses the same staff/queue-view permission as the dashboard.
Reviewing an issue, changing alert preferences and sending a test require queue
change permission and CSRF-protected POST. Mark reviewed preserves history; a new
occurrence returns the issue to attention. Stale review counts and preferences
cannot silently overwrite newer data. GET does not send messages or mark reviewed.

The existing email worker queues up to 20 unreported issues in one owner email,
at most once per hour, using database row locking and the existing durable outbox.
The oldest unreported issue is handled first. Unreported issues beyond the batch
remain eligible. No further email is queued without a new occurrence. A queued
alert records its exact included occurrence count atomically with outbox creation.
Unknown send outcomes are held, never automatically resent. Disabling alerts
cancels pending error alerts when the worker next handles them, while retaining
history and leaving the independent monitor untouched. Tests are separately
throttled to one every five minutes and never trigger a real server error.

The recipient is the existing `INTAKE_OWNER_EMAIL`; no new provider, permissions,
credentials, service or hosting charge is introduced. Email alerts default on for
this user-approved feature. Existing logs are not retrospectively imported.
Application records appear only after deployment. Existing logging behavior is
unchanged: this feature's privacy limit applies to the incident records and alert
emails, not to arbitrary messages written by other code to Render logs.
