# Booking access for CRM contacts

Booking still requires a valid verified-email client session. The public sign-in response remains the same whether or not that email belongs to a client.

For the verified email, eligibility is evaluated on every booking page and again when reserving a time:

1. An explicit BookingAccess allow/block takes precedence.
2. Otherwise, an exact email match (ignoring case and surrounding whitespace) on a non-archived ClientContact attached to a non-merged client permits booking.
3. Unknown emails cannot book. No name, company, phone or email-alias matching is used.

CRM-derived eligibility creates no permanent BookingAccess grant and no ProjectAccess grant. Adding/importing a contact or correcting its email takes effect immediately, including for an existing verified session. Archiving a contact or changing its email removes this implicit path. Explicit manual grants and existing grants established by verified work submissions keep their prior behavior.

Staff can still use Work queue -> Client appointments -> Booking access for existing clients to enable an external email or block any email, including a CRM contact. Revoking access prevents new bookings/reschedules but does not prevent cancellation of an existing appointment. A new submission or another verification never undoes an explicit block.

No database migration, production backfill, email campaign, calendar change or broader project access is needed. The existing Google Calendar availability checks, 24-hour notice, two appointments per day, Tuesday-Friday 10 AM-2 PM Eastern, meeting types and 30-minute buffer remain enforced.
