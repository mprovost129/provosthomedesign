# Verified-email booking and initial consultations

Anyone with a valid verified-email session can book an available appointment. CRM membership, prior work, manual email enablement and consultation approval are not prerequisites. Legacy BookingAccess allow/deny records and ClientContact.booking_requires_approval flags remain as history but no longer restrict booking. Staff can cancel individual appointments with the existing appointment permission; project/document access rules are unchanged.

The public booking page accepts an email directly. The optional `/request-consultation/` form collects name, email, phone and project description with terms consent, CSRF, session-bound tokens, reCAPTCHA and IP/email rate limits. A consultation request does not reserve a slot or add work to the project queue.

An unverified consultation queues only a single-use email link, valid for 24 hours. Verification creates/reuses a CRM inquiry contact without granting staff, project or document access, records `ready`, notifies the owner and redirects the verified session to booking. Existing contact details are not overwritten. GET requests/email scanners do not consume tokens. Retries do not duplicate contacts or owner notices. The original browser can request another link before verification.

Previously pending, approved or declined consultation records do not block a valid verified session. Visiting the old consultation URL while verified redirects directly to booking. No mass approval, historical data rewrite or bulk notification is required. The staff page lists verified inquiries without approve/decline or enable/disable controls. Stale staff action POSTs explain the new policy without changing access or sending decision emails.

The existing appointment flow still enforces identity and ownership, CAPTCHA/rate limits, Eastern time, 24-hour notice, business hours, meeting/buffer lengths, daily/client limits, idempotency and calendar conflict checks. Confirmation still waits for the calendar worker; verification alone does not create an appointment.

Apply migration `0040_consultation_ready_to_book` before starting the new web and worker code. It expands the state constraint to accept `ready` and preserves historical states and records. Existing queued emails may contain the older approval wording, but their verification links now lead to booking. No real mail or calendar events are created by tests.
