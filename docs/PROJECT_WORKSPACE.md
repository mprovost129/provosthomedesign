# Project workspace and FreshBooks handoff

Staff open **Projects** from the queue, or **Workspace** on a queue job. A project brings together the original submissions, private files, connected clients, tasks, planning notes, proposals, and recorded emails. The queue remains the source of job ordering and work status.

## Proposals

1. Open a project and choose **New proposal** or **Estimate this work**.
2. Select the billing client. Optionally copy an editable template. Enter your scope, deliverables, exclusions, included revisions, timing, terms, and service quantities/rates. No fees, deposits, tax rates, or contract terms are assumed.
3. Save and preview the draft or download its PDF.
4. Choose the recipient and explicitly confirm **Email proposal**. The existing durable Gmail outbox sends a private invitation and full text. The sent snapshot remains unchanged even when contacts or project details change later.
5. The recipient can view/download only that proposal and accept or request changes. Acceptance records the name, invited email, time, and exact version, and queues owner/client receipts. This does not expose private notes, all project files, or other client records.
6. Create a draft revision to make changes. Old versions and acceptance records remain available to staff. A previous version cannot be accepted while a draft revision is being prepared or after it is superseded/withdrawn.

Private links use fragments, hashed tokens, a session exchange, no analytics, no-store headers, and a maximum 30-day expiry capped by the proposal's valid-through date. Form pages use same-origin referrers so native HTTPS form submissions pass CSRF checks; PDFs and other private responses suppress referrers. Forwarding a private link gives its recipient access: the acceptance is evidence of action by the holder of the invitation, not independent identity verification or a formal e-signature service.

## Time tracking

Choose **Track time** on a queue job to select its project and job, then choose the work type, optional description, and billable flag. **Start timer** persists a server-owned timer for the signed-in staff member; navigation, closing the browser, and deployment do not stop it. Only one timer per person can run. **Stop and record time** records elapsed server time once, including when two tabs retry. **Discard timer** requires confirmation and records no hours.

Open **Time entries** from the queue or **Time** in a project to add missed time by date and minutes, correct entries, remove entries, or restore a removal. Corrections retain original timer timestamps and record changes in project activity. Saved time totals distinguish billable/nonbillable hours and compare actual time against the project's estimated hours. Running timers are excluded from saved totals. A timer over 12 hours prompts review; it does not stop or bill automatically.

These records are private to authorized staff. Preview and read-only staff cannot change time. Timer controls in the queue operate without navigation and preserve unsaved queue edits. Manual entries and corrections use version checks and request identifiers to prevent duplicates and stale overwrites. Removed records remain recoverable. Time tracking does not email clients, change queue order/status, send time to FreshBooks, create invoices, or charge payments. Review recorded time before using it for hourly billing in FreshBooks.

## FreshBooks deployment configuration

Register a developer app in the existing FreshBooks account. HTTPS callback:

`https://www.provosthomedesign.com/work-projects/billing/callback/`

Scopes: `user:profile:read`, `user:clients:read`, `user:invoices:read`, `user:invoices:write`, `user:online_payments:read`, `user:online_payments:write`.

Set on **both** the Render website and the existing queue scheduled job:

- `FRESHBOOKS_ENABLED=True`
- `FRESHBOOKS_CLIENT_ID`
- `FRESHBOOKS_CLIENT_SECRET`
- `FRESHBOOKS_REDIRECT_URI` (the callback above)
- `FRESHBOOKS_TOKEN_ENCRYPTION_KEY` (one newly generated Fernet key, identical on both services, kept in private environment settings)

Never commit credentials or token-protection keys. Back up the encryption key securely: losing it requires reconnection. OAuth tokens are encrypted in the database. Only the site owner/superuser can authorize, choose a business, or disconnect. FreshBooks refresh tokens rotate once; database locking serializes web/worker refresh. Interrupted refreshes require reconnection rather than replaying an old token.

## Billing workflow

1. Authorize **FreshBooks** from the Projects navigation and explicitly choose the business.
2. Connect a CRM client to an existing FreshBooks client after reviewing name/email/address. No clients are created or contact details overwritten automatically. If no matching billing client exists, create it in FreshBooks first. Distinct CRM clients cannot share one FreshBooks client link without reviewing/merging their CRM records.
3. On a current accepted **fixed-price** proposal, explicitly request **Prepare FreshBooks draft**. One proposal can produce one invoice draft. Hourly estimates, zero totals, deposits, installments, and change-order adjustments are reviewed in FreshBooks; the website does not treat estimated hours as earned billable time.
4. The existing scheduled job creates the draft. It applies the account's existing invoice payment options when available. If payment configuration is unavailable or requires a different FreshBooks plan/add-on, the draft remains intact with a review message. No subscription or payment gateway is purchased or enabled by this integration.
5. Review taxes, due dates, client details, payment options, and pricing in FreshBooks, then send it there. The website does not send invoices, charge cards, or auto-create invoices on proposal acceptance.
6. Linked statuses/balances refresh about every 15 minutes during normal worker runs; **Refresh billing status** is also available. Completing queue work does not mark an invoice paid, and invoice payment does not close a queue item.

Creation requests are durably recorded before making the API call. An interrupted/ambiguous POST is held as **Check FreshBooks**, never automatically retried. **Check for existing invoice** reconciles the exact stored project marker, customer, currency, and original total; if no single match is found, review the FreshBooks account manually. Definitively rejected drafts remain **Needs review**. This deliberately avoids guessing that an uncertain create failed.

API-disabled installations retain the project/proposal workflow. Production billing activation requires real credentials and user OAuth consent; local checks use fake providers and never create customer invoices.

Official API references: [OAuth](https://www.freshbooks.com/api/authentication), [clients](https://www.freshbooks.com/api/clients), [invoices](https://www.freshbooks.com/api/invoices), [payment options](https://www.freshbooks.com/api/online-payments).

## Project reports

Open **Reports** in the queue or project navigation. The private, read-only report compares estimated hours in the project plan with saved time. It splits billable, nonbillable, and revision hours; revision hours are included in actual hours. Running timers and removed time do not count. Time already recorded against a removed job remains part of its project history, provided the project still has a visible job. Projects without any visible jobs and legacy jobs without a linked project are excluded.

Filters select active, completed, or all projects, over-estimate projects, missing estimates, or completed projects needing billing review. Search accepts project/address, job reference, client/email, and proposal number. Totals cover all matching projects across pagination. Over-estimate flags compare exact seconds before rounding display hours. A missing estimate is different from an explicit zero. A completed project has completed work and no active jobs; canceled/declined/duplicate jobs alone do not count as completed.

Invoice cards show only linked FreshBooks records, including their last synchronization time and currency-specific outstanding balance. Snapshots older than 24 hours are marked. Review billing flags completed projects without linked invoices or with drafts, failed/incomplete links, missing syncs, or voided/deleted invoice status. An invoice may exist separately in FreshBooks; review there before creating another. The report does not create, send, or synchronize invoices, infer profits or hourly charges, or change queue position/status.
