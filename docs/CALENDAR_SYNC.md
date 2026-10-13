# Google Calendar and appointment synchronization

Google Calendar remains the staff calendar. Website appointments have one owned meeting event and one owned 30-minute buffer. The booking app and authenticated queue show the same meeting time and status. Personal Google Calendar events are used only as busy periods; they are not imported as client appointments.

## Daily use

- Website bookings, cancellations and client reschedules write to the connected primary Google Calendar through the existing worker.
- Delete the **Client meeting** event in Google to cancel its website appointment. Once Google confirms its absence and the owned buffer is removed, the app marks it canceled and frees its local reservation.
- Move a website meeting in Google to change the app's date/time. Extend its end time when a meeting needs longer. The app records the observed time and moves the 30-minute buffer after the actual end. It sends a client/owner update after the new calendar state is verified.
- The buffer belongs to the meeting. Deleting only the buffer does not cancel the meeting; the worker repairs it. Delete the meeting to cancel both.
- New website bookings remain 30 minutes. Client reschedules preserve an owner-extended meeting's duration and require the full meeting plus buffer to fit Tuesday–Friday, 10 AM–2 PM Eastern. Owner changes made directly in Google can be outside public booking hours. Public booking still enforces 24-hour notice, two appointments/day and one upcoming appointment/client.
- Future canceled meetings and meetings whose recorded start is within the past seven days are also checked for a Google restore or move. Older history is retained without continual Google polling. Restoring a recent/future owned Google meeting restores the website appointment after its time/buffer are verified. Restoring an obsolete original after a reschedule can produce two Google meetings; both are faithfully represented until the owner cancels the unwanted one.

The scheduled job runs once a minute and checks appointments whose last reconciliation is at least 45 seconds old. Changes normally appear within **1–2 minutes**, with extra delay possible during provider failures or a large queue. Refresh an open appointment page after changing Google Calendar. Availability is read from Google again when opening dates and when reserving a time; a date selection does not reserve anything.

## Integrity and recovery

The app accepts only single timed events with its stored event ID and matching private appointment marker. It preserves the recorded client/email/project identity; Google summary, attendee or description edits never transfer CRM or project access. Timed meetings may be 1 minute to 8 hours; all-day, repeating, malformed or foreign-identity changes require review. Conflicting moves retain the observed Google time and local hold with a visible warning until resolved. No personal event is overwritten or deleted.

Known calendar absence is distinguished from a lost calendar connection. A canceled record stays canceled while being watched, even if Google is unavailable. A restored meeting is held only after its owned main event/time are observed. Deletion cleanup removes only the owned buffer and rechecks that the meeting did not reappear. A concurrent restore cannot be deleted as a side effect of stale cleanup.

Buffer updates use conditional `If-Match`/ETag writes and verify the result. Deleted Google IDs may remain tombstones: a replacement buffer ID is persisted before insertion and reused through uncertain outcomes. A lease token and booking-row lock prevent competing/stale workers from publishing another worker's result. Google calls occur outside database transactions. New time/duration data are held locally before buffer writes; failures never falsely report a successful update or release an unknown active reservation.

Each observed change has a monotonically increasing notification generation. Confirmed changes/cancellations/restores create at most one client/owner email pair per generation. Superseded pending messages are canceled before delivery. Ambiguous SMTP outcomes still use the existing reconciliation safeguards and are not automatically retried. Appointment history and audit records remain preserved.

Migration 0037 adds buffer retry identity and notification generation fields, with database defaults for rolling deployment. It replaces the fixed 30-minute database duration constraint with an eight-hour ceiling; positive duration and the 30-minute buffer constraint remain. New public reservations still use 30 minutes. No new credential, Google scope, calendar, paid service or scheduled job is required. Roll back application code without dropping these fields/history; reverting to the old fixed-duration constraint is unsafe after longer owner-edited meetings exist.

Google API references: [Conditional writes and ETags](https://developers.google.com/workspace/calendar/api/guides/version-resources), [event patch semantics](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch), [event status and cancellation](https://developers.google.com/workspace/calendar/api/v3/reference/events).
