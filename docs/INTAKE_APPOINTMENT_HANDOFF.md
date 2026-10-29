# Booking after a work submission

Both new submissions and updates have an optional Request an appointment
checkbox when booking is enabled. The existing intake validation, upload
transaction, queue allocation and two receipt notifications complete before
redirecting to booking. Leaving the checkbox clear retains the usual receipt
page. The accepted answer is saved as Yes in the submission snapshot, and the
existing client receipt includes a booking link and explains that no meeting
has been reserved yet. No additional automatic email or appointment is created.

Booking shows the accepted request ID and original position, a link back to its
confirmation, and a reminder that the upload is already saved. The submitting
browser receives a four-hour context tied to its existing recent-request
allowlist. Possessing that context never verifies an email or grants project
access. The email sign-in field is filled in; calendar availability still needs
normal email verification. A different verified identity must verify the upload
email or explicitly continue without that submission.

After verification, booking fills in the submitted name, phone and discussion
subject. The project is selected only if already authorized for that verified
email. Contact information from the submitted snapshot takes precedence over
an unrelated recent request. A successful reservation clears the handoff;
failed validation or calendar errors retain it. The client can also clear the
context without deleting work or changing their verified identity.

The usual booking limits, Google Calendar checks, meeting method selection,
terms, reCAPTCHA and CSRF protections apply. New clients need no CRM membership
or staff approval. Existing sign-in limits and reservation idempotency remain.
Repeating an intake POST uses the original accepted appointment answer, so a
double click cannot duplicate work, receipts or a meeting, or change the saved
preference. Draft recovery retains the optional choice as an ordinary boolean.

No database migration, new service, external permission or infrastructure
setting is required. Tests cover opt-in/out, both form paths, invalid input,
email verification, different identities, privacy, expired/deleted contexts,
receipts, retry behavior, draft saving, disabled booking, CSRF, and successful
reservation cleanup. The fictional browser preview also exercises the complete
submit, verify, select date, choose method and reserve journey without external
email or Google Calendar operations.
