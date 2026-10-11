# Client email, reminders and CSV contacts

Staff use the existing authenticated Work Queue and Clients pages. There is no additional Google authorization, mailbox access or paid service.

## Email editor

Click a project name, client name or Email on a queue row. Client records also have Email client. A same-origin popup preserves unsaved queue edits. Select one or more active contacts belonging to that client, optionally add CC and BCC addresses, and choose a project subject or type your own. The rich editor supports bold, italics, underline, strike, headings, lists, quotes, safe links, text colors and alignment. Each message can include or skip the shared saved signature from Email settings.

Email uses Django's existing Gmail SMTP configuration and the existing durable notification outbox/background worker. Sends are explicit staff POST actions with CSRF protection and a unique message ID; a repeated save does not queue another email. At most 25 distinct recipients are permitted. BCC goes into the delivery envelope, not message headers. The Sent state means the mail provider accepted the message; it does not guarantee delivery or reading. Replies continue in Gmail. This is an in-app composer, not an embedded Gmail inbox or reply sync.

Message recipients, subject and sanitized rich/plain content are kept privately in client activity; original intake answers and queue status/position are untouched. The HTML allowlist removes scripts, remote images, embeds, unsafe attributes and unsafe link schemes. Quill 2.0.3 is vendored from its integrity-verified npm package with its BSD license. Unknown provider outcomes remain held for reconciliation and are not automatically retried.

## Morning reminders

Email settings provides an on/off checkbox. The existing once-per-minute worker queues at most one actionable summary per date, weekdays from 8 AM Eastern (with catch-up until noon). Summaries include open client follow-ups due today/overdue, active job follow-ups and overdue committed deadlines. Only the configured intake owner receives the email. Private task notes are omitted. No email is queued if no items need attention. Switching off cancels unsent pending summaries. The database default is off until enabled by staff.

## Search

Client directory search includes existing identity/project fields plus private client notes, activity notes, follow-up titles/notes, job notes and composed email subjects/plain text. Merged historical client profile notes resolve to the current client.

## CSV import/export

Clients → Import accepts UTF-8 CSV, up to 500 contact rows and 1 MB. Supports the supplied Organization, First Name, Last Name, Email, Phone, Address Line 1, Address Line 2, City, Province/State, Country, Postal Code, Notes layout. Blank template and export also include Full Name, Client ID, Contact ID and optional Role. Common column aliases are accepted; unknown/duplicate columns and malformed CSV are rejected, rather than silently losing fields. Postal codes and phone numbers remain strings.

Upload creates a private, user-owned preview only. Exact normalized email matches select an existing client; exported IDs support round trips. Name/company/phone or archived-email candidates require explicit Connect, Different client or Skip choices. Repeated identical contacts/email rows reuse their first result; multiple contacts with the same new organization share the visibly previewed client. Invalid rows are shown as skipped with their reasons. A confirmation checkbox is required to apply the reviewed preview.

Import holds the shared queue lock, rechecks matches/versions, expires previews after two hours, and commits all rows together. Retry of an applied batch returns its recorded result. Existing nonempty contact and billing fields are preserved; blank fields are filled and distinct notes are appended. Existing jobs, grants, project links and queue order are unchanged. Import does not send receipts or client emails. CSV source rows and change events remain privately recorded. Name-only matches are never silently merged. Export includes active clients/contacts and defends against spreadsheet formula injection; it does not include archived contacts or projects.

No import is applied without review. For ambiguous people/company names, staff decide the association based on actual identity, not merely a shared project address.
