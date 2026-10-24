# Reviewed invoice test delivery

Open **Invoices → Billing test area → test invoice → Review & email test copy**.
Select an active email contact belonging to the invoice client and write the
subject/message. The next screen shows the intended contact, actual owner inbox,
message and complete billing document. Only explicit confirmation queues mail.

## Scope

This release sends **test copies only to INTAKE_OWNER_EMAIL**. There is no editable
To, CC or BCC and no live-client sending switch. The intended contact is recorded
for recipient review but is never an actual recipient. Subjects, branded HTML,
plain-text alternatives, public pages and PDFs clearly state TEST ONLY and no
payment due. FreshBooks remains responsible for real invoices and payments.

The signed review expires after 15 minutes and is bound to its staff actor,
invoice, invoice version, intended contact/name/email, subject/message and owner
address. The mutation service rechecks current contact and owner settings. An
invoice edit or a payment/credit recorded after review requires a fresh review.
Concurrent confirmations use one nonce and one transactional outbox record.

## Private link and history

Each copy freezes its invoice details and reviewed message. Its private link has
a seven-day expiry and a random 256-bit secret. The database stores a digest;
the raw secret is only retained in the pending mail body until the existing
outbox clears it on successful delivery. Link secrets are in URL fragments,
removed before the browser submits a native CSRF-protected form. Access pages
use same-origin referrers to support Django HTTPS CSRF checks, including Safari;
invoice pages/PDFs disable caching and cross-site referrers and request noindex.
They do not include analytics scripts or access to a whole client account.

The resulting session is bound to that delivery, and each view/download checks
expiry, revocation and invoice void state. Opening a link is recorded once; it
is not proof that a person read or accepted an invoice. The public invoice has
the frozen bill and current aggregate test balance only. Internal accounting
notes, payment references, staff events and other client projects stay private.

Mail history distinguishes pending, sending, provider-accepted, failed,
uncertain and canceled outcomes using the existing durable outbox. Provider
acceptance does not prove inbox delivery. An unknown outcome blocks another
copy until the provider result is reconciled; this release does not add a blind
retry control. A new explicit copy after a completed result rotates older links.

Staff can revoke a link and cancel pending email from its delivery record. The
worker also cancels pending copies if the link expired, invoice was voided, owner
address changed, or To/CC/BCC were altered. Revocation cannot recall a message
already being sent or delivered; it still disables future link access. Downloaded
PDFs cannot be recalled. Historical records and original document snapshots remain.

Migration 0052 adds a delivery table only. No existing bill is sent automatically.
The scheduled job uses its existing email settings and permissions. Opening the
staff pages, a new review or a PDF does not itself send mail. The ledger and
mail preview can be checked with fictional local fixtures and a local mail backend.

## Next billing phase

Review this experience with owner test copies. Actual invoice issuance requires
separate live numbering, opening balances, owner-approved cutover and recipient
delivery controls. Online card/ACH checkout and verified payment webhooks are a
separate phase; no test receipt or payment status here charges money or confirms
a real payment. Existing FreshBooks integrations remain active and separate.
