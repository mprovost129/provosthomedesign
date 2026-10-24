# Billing receipt emails and delivery workspace

Open **Invoices → Billing test area → Billing emails**. This is an owner-only sandbox communication layer; FreshBooks still issues all real invoices and collects real payments. It adds no Stripe scopes, credentials, public document permissions, third-party services or hosting charges. It never emails the intended client.

## Receipt email controls

Automatic receipts default off. The account owner may enable or pause them in Billing emails. Enabling records a fresh activation timestamp, so existing payments are not backfilled. A successful verified payment creates its immutable receipt independently of email. The existing minute job queues eligible future receipts and sends them through the existing durable Gmail outbox, usually on the next job run. Pending/failed/unverified ACH payments do not get a receipt. Email failure never rolls back accounting.

Turning automation off cancels automatic messages still pending. An in-flight send cannot be recalled. Re-enabling starts a new future-only window; canceled messages and older receipts stay closed rather than flooding the inbox. A reviewed manual receipt email remains independent of the automatic setting.

Each receipt has at most one email delivery record. The owner may review an existing receipt, recipient, amount, project and PDF and explicitly queue its owner test copy. A signed fifteen-minute review binds the receipt, acting owner and current configured inbox. A changed inbox or tampered/expired review cannot silently redirect it. A previously reviewed invoice link becoming revoked, expired or void blocks or cancels the pending receipt email. Every worker rechecks the frozen recipient and rejects added To/CC/BCC recipients.

## Message and attachment

The branded HTML/plain-text email includes receipt number, invoice number, frozen client/project, original verified amount, payment date and method. The attached original-brand PDF is generated from the receipt allow-list; it contains no card/bank details, provider secrets, payment identifiers or internal notes. It clearly says TEST ONLY and no real money received. It records the original payment, not the current balance after later refunds or credits. No new bearer link is created. Successful delivery clears outbox bodies; the immutable receipt and activity record remain.

## Delivery recovery

Billing emails shows receipt delivery counts, searchable receipt/invoice/client/project history, recent invoice email copies and recorded owner actions. Staff with existing workspace view permission can inspect it; only the active staff superuser may change automation, send, retry or resolve a receipt. Staff-preview mode cannot mutate it. All mutations use normal CSRF protection.

Known-unsent failures (PDF preparation, explicit recipient rejection or zero backend acceptance) may be retried after owner review. Unknown SMTP outcomes and interrupted sends are never automatically retried. The owner checks Gmail, then either confirms receipt or closes without resending. A sending or sent message cannot be sent again through the recovery UI. Stale action forms conflict instead of overwriting newer results. Shared queue locking and the one-to-one receipt constraint prevent concurrent duplicate queue records. The existing outbox reserves each send and preserves its stable message ID.

The center's receipt-email counts describe all receipt deliveries; search filters the receipt and invoice-copy lists. Recent activity remains a global audit. Invoice email history records link openings, not proof that a client read an invoice. This release also adds an explicit fresh-review link after an expired invoice email preview.

## Validation and rollout

Use fictional local data and in-memory email first. Check owner/staff/anonymous access, CSRF, signed reviews, changed recipients, privacy gates, future-only activation, pause/re-enable, PDF content, SMTP/PDF failure, uncertain outcomes and concurrent requests. Publish only after the complete app suite and appropriate browser checks. Keep automatic receipts off on rollout; a reviewed owner test receipt can verify actual Gmail attachment delivery without issuing another invoice or moving money.

Production receipts, customer emails, reminders, live keys and switching away from FreshBooks are separate releases. No reminder or client-facing automation is enabled by this change.
