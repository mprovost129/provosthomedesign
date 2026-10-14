# Booking meeting types

Clients must choose In person, Phone, or Google Meet when booking or rescheduling.
Existing appointments retain their unspecified meeting type until the client selects one during rescheduling.

In-person bookings snapshot the configured CONTACT_ADDRESS at reservation time. The form, client appointment page, staff queue, calendar location and both confirmation emails provide the office address. Phone appointments state that Mike calls the submitted phone number.

Google Meet uses the existing Google Calendar authorization. Only the primary meeting event receives conferenceData.createRequest, with a stable appointment-specific requestId and conferenceSolutionKey.type=hangoutsMeet. Each rescheduled appointment creates its own unique conference. No guests are added automatically; the app sends its existing confirmation emails with the verified join URL.

Calendar inserts and conference patches use conferenceDataVersion=1. Existing owned events without conference data are patched with If-Match, preserving their other fields. A pending, failed or unverified conference holds the appointment and suppresses confirmation emails. The scheduled worker retries reconciliation. Persistent failures appear under Client appointments in the staff work queue. Check calendar/account Meet availability and add a conference in Google Calendar before retrying if necessary.

Only HTTPS video entry-point URLs under meet.google.com with the expected meeting-code path are used. Provider response text and credentials are never exposed to clients. Existing cancellation, Google time changes, buffers, booking access and terms requirements remain in effect. In a reschedule, the old meeting is removed only after the new conference is verified.

Database migration 0038 adds meeting_method, meeting_address and meet_url with persistent database defaults so the old worker can continue running during deployment. Apply the migration before the new worker starts.

Google references: https://developers.google.com/workspace/calendar/api/v3/reference/events/insert and https://developers.google.com/workspace/calendar/api/v3/reference/events.
