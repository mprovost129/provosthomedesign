# Project workspace and FreshBooks handoff

Staff open **Projects** from the queue, or **Workspace** on a queue job. A project brings together the original submissions, private files, connected clients, tasks, planning notes, proposals, and recorded emails. The queue remains the source of job ordering and work status.

## Add source files to an existing project

In the project's **Plans and pictures** section, authorized staff can choose **Add files**, select an existing job in that project, upload documents, and choose **Add files to project**. The existing upload control accepts up to ten files of 100 MB each (PDF, JPG, PNG, WebP, DOCX, XLSX), validates their content, and stages them privately before saving. Resolve or remove failed uploads before saving.

This creates a staff attachment batch on the selected existing job, not another work request. It leaves queue order, status, original submission answers, and completed deliveries unchanged. It does not publish documents or send any email. These source files are visible to authorized staff on reopening the project, and excluded from client tracking and client downloads. A project activity entry records who added them. Read-only staff and preview mode cannot add files.

The entire batch is attached in one database transaction. Retrying the same form after a failure or lost response does not duplicate attachments; a changed batch after a successful save requires opening Add files again. Unattached staged objects retain the existing expiry/cleanup behavior.

**Storage:** Add files uses the existing `intake_private` storage (private S3 in production), as approved by the owner. Historical Google Drive files retain their existing staff-only links and Google permissions. This feature does not introduce Drive writes or new credentials.

**Deployment:** this feature adds no models or migrations. Run the repository CI checks before merging to `main`; the documented Render workflow auto-deploys `main` to `provosthomedesign` (`srv-d8cps8urnols739r24sg`). Verify the exact deployed commit and the authenticated Add files page after deployment. Do not upload real client files or create production test records for verification.

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

Client merge previews include the FreshBooks connections that will move or remain.
A connection moves to the kept CRM client when that client has no connection for
the same FreshBooks business. Conflicting customer connections block the merge
until reviewed. Merge undo restores moved connections only while client versions
and the recorded billing connections are unchanged. Linking a billing customer
updates the client's audit history/version, invalidating older merge previews and
undo actions. These operations do not change existing invoices in FreshBooks.

Official API references: [OAuth](https://www.freshbooks.com/api/authentication), [clients](https://www.freshbooks.com/api/clients), [invoices](https://www.freshbooks.com/api/invoices), [payment options](https://www.freshbooks.com/api/online-payments).

## Project reports

Open **Reports** in the queue or project navigation. The private, read-only report compares estimated hours in the project plan with saved time. It splits billable, nonbillable, and revision hours; revision hours are included in actual hours. Running timers and removed time do not count. Time already recorded against a removed job remains part of its project history, provided the project still has a visible job. Projects without any visible jobs and legacy jobs without a linked project are excluded.

Filters select active, completed, or all projects, over-estimate projects, missing estimates, or completed projects needing billing review. Search accepts project/address, job reference, client/email, and proposal number. Totals cover all matching projects across pagination. Over-estimate flags compare exact seconds before rounding display hours. A missing estimate is different from an explicit zero. A completed project has completed work and no active jobs; canceled/declined/duplicate jobs alone do not count as completed.

Invoice cards show only linked FreshBooks records, including their last synchronization time and currency-specific outstanding balance. Snapshots older than 24 hours are marked. Review billing flags completed projects without linked invoices or with drafts, failed/incomplete links, missing syncs, or voided/deleted invoice status. An invoice may exist separately in FreshBooks; review there before creating another. The report does not create, send, or synchronize invoices, infer profits or hourly charges, or change queue position/status.

## Project checklists and task attention

Open **Tasks** from the queue or project navigation. Filter by active/all projects and open, overdue, due today, next seven days, undated, completed, or removed tasks. Counts follow project/search filters; the seven-day range includes today. Projects with no visible jobs are excluded; connected submissions do not multiply task counts. Dates use the configured local timezone. This is a staff planning view; the work queue still owns job status and client position.

Choose **Reusable checklists** to create a named list of 1–30 steps, one per line. Edit or archive it later. No default technical scope or deadlines are assumed. Editing a list never rewrites tasks already applied. Archived lists remain accessible for editing and recovery but disappear from project selection.

From a project's **Tasks** section, choose **Add a checklist**, select a list, and preview it. Uncheck unwanted steps and adjust each title, optional date, and private notes before applying. Existing nonremoved titles are matched after Unicode, whitespace, and case normalization; completed steps are also skipped. Removed steps can be restored rather than recreated, or a checklist can add a new step of that name when intentionally reapplied. Different named steps remain separate. Applications preserve their source version and reviewed snapshot; repeat confirmation cannot create another set, and concurrent applications skip existing steps.

Edit a task to change its title, date, or notes. Mark complete or reopen it from the task list or project. Removal requires confirmation on the edit page; restore from **Tasks → Removed** or the task edit link. Saved task changes use version checks, share the queue's existing serialization lock, and record project activity. They do not email clients, create invoices, change project estimates, move queue positions, or change queue job statuses.

Apply additive migration **0028_projectchecklist_checklistapplication** before serving this feature. Existing projects, jobs, files, proposals, and time records are preserved. No new external services, credentials, OAuth scopes, or worker settings are needed.

## Proposals overview

Open **Proposals** from the queue or project navigation to see one row per proposal, based on the current draft or sent version. Search by proposal number/title, project/address, client name, job reference, or current recipient/job email. All projects is the default and preserves proposal history even when its jobs were removed. Active projects requires at least one visible job with an open queue status. Multiple connected jobs and older sent versions do not multiply counts.

Filters cover drafts, queued email, awaiting response, changes requested, expired links, links expiring within seven days, email problems, accepted, withdrawn, and needs review. Counts follow search/project filters; status only narrows the list. Needs attention includes current requested changes, expiration, missing invitation data, failed/canceled email, and uncertain delivery. A draft revision takes precedence over the previous version's state. Accepted/withdrawn versions are never reclassified as expired or awaiting response. A pending/sending message is **Email queued**, rather than awaiting a response. Expiration uses the actual private invitation cutoff, including the valid-through cap.

The row shows proposed USD fees (hourly amounts are estimates), validity, recipient, preparation/first-view/acceptance dates, and actual saved email state. Sent means accepted by the email service, not proven inbox delivery. First-view records can include automated security checks. Private invitation URLs, tokens, email bodies, and internal project notes are never included in the overview.

Open the existing proposal editor for any send, revise, withdraw, or FreshBooks action. Viewing/filtering the overview performs no writes, sends no reminders or client messages, creates no invoices, and changes no queue status/order. It uses existing staff permissions and no-store headers. No migration, external service, credentials, or worker setting change is required.


## Invoices overview

Open **Invoices** from staff navigation or the queue to review locally linked FreshBooks records. Search by invoice/proposal number, project/address, client name, job reference, or email. All projects is the default and preserves billing history for closed/removed jobs. Active projects requires visible open work. Each invoice appears once regardless of connected jobs or proposal versions.

Status filters cover preparing drafts, drafts, outstanding excluding overdue, overdue, paid, billing review, stale snapshots and voided/deleted invoices. Overdue uses FreshBooks’ saved v3 status, never an inferred due date. Paid requires the saved paid/autopaid status and zero outstanding balance. Drafts and closed invoices are excluded from outstanding totals, as are incomplete/invalid balances and payment-problem statuses. Totals group by currency and include all search/scope matches before the status filter/pagination. Stale snapshots and failed refresh/payment setup are flagged; the last checked date is not necessarily the date of the last successful balance refresh. No raw API errors, request payloads, or credentials are exposed.

Completed projects with no local invoice or a draft/incomplete billing record appear in a separate review section (first 10, with a link to the full Reports list). Its count follows search/project filters independently of invoice status. No local link does not establish that no invoice exists in FreshBooks. Invoices created separately there are not imported or included automatically.

Open the exact staff invoice in FreshBooks using its account and invoice ID, or review/refresh from the existing proposal billing section. All invoices opens the FreshBooks invoice list. The overview is GET-only under existing staff permissions and no-store headers. Reads make no provider calls or writes and send no invoices, reminders or payments. No migration, credential, external service or worker change is needed. Official status reference: https://www.freshbooks.com/api/invoices .

## FreshBooks CSV invoice history

Use Invoices → Import CSV to upload the original FreshBooks line-item export (UTF-8, 18 headers, maximum 2 MB/10,000 rows). Invoice numbers remain strings; grouped metadata must agree. Raw line values, description breaks, dates, tax/discount fields and duplicate billable lines are preserved. Monetary calculations use Decimal and sum only the exported Line Total; no paid/outstanding amounts, provider invoice IDs, proposals or queue work are invented.

The preview binds the selected billing business, staff actor, grouped invoices and current client matches with an expiring signature. Unique exact CRM client names are recommended. Contact name/company matches are suggestions only, and ambiguous/unmatched names remain unassigned. Confirm the reviewed choices to apply the new history. Client profiles/contact details remain unchanged; associated clients get an activity record and version bump. Invoice identity is billing account plus invoice number, enforced by a database unique constraint. Existing live links and unchanged imports are skipped. Changed imported facts are held, never overwritten. Repeated application returns the original result. Import writes are atomic under the queue lock and revalidate matches/business/duplicates to reject stale reviews.

Imported history is a separate source under Invoices. It shows exported status and summed line amounts, not current balances or amounts collected. The historical tables have no relationship to the FreshBooks create/sync worker, so import never creates/sends invoices, takes payments or sends emails. Existing live invoice records and all queue data are preserved. Link an existing client/project from a history record's association form; this uses version checks and records before/after associations. A client record links to its history (including associations from merged descendant profiles); projects link to explicitly associated history. Neither matching nor associations grant client access.

Only the importing staff account can undo its batch, hiding records while preserving source facts and associations. The original batch can restore them. Reimporting a hidden invoice directs staff to recover the original batch rather than create a duplicate. Detail pages show hidden records read-only. Private staff permissions, preview restrictions, CSRF protection and no-store headers apply.

Apply additive migration 0029_invoicecsvimport_historicalinvoice_and_more before serving these pages. No existing table/data, provider permissions, credentials, paid services or worker configuration changes are required. No source CSV or customer financial data is committed to the repository.


### Imported invoice live balances

Imported invoices retain their CSV facts. HistoricalInvoiceBalance stores separately verified FreshBooks identity, status and amounts; no proposal, draft, payment or message is created. InvoiceHistorySync performs a resumable daily scan through the existing sync_freshbooks_billing worker, four bounded GET calls per run. Staff can enqueue a check from imported history (five-minute manual cooldown). Each page is leased, verified for business/pagination consistency, and staged; snapshots apply only after a complete scan. Changed invoice date/currency/ID, duplicate identities, missing/deleted records and malformed amounts are held for review. Failed scans preserve the previous successful timestamp and values. Active and archived invoices are included by FreshBooks' default list behavior; deleted records do not become receivables. Billing profiles are read only for unassigned records, and unique email/existing billing-link suggestions require manual association review. This feature adds no provider scopes and no financial writes. CSV amounts are never presented as current receivables. Balances older than 24 hours, failed record checks, or a different/disconnected business are visibly marked for rechecking.


## Company-specific contacts and hourly billing

Contacts may use the same email under separate companies. Uniqueness is now per client record; duplicate emails within one company remain blocked. Repeat intake matches email plus the submitted company (or homeowner kind). A unique exact company name groups different contacts privately without granting access to other jobs. Ambiguous company names are not collapsed. Existing historical migration matching remains frozen, and this release does not reassign old work or merge companies.

From a client contact choose **Connect this person to another company**. Select an existing company and confirm the reviewed association. Profiles remain independently editable, and company jobs, invoices and public access remain separate. Existing contacts with that email retain their details. Linking uses signed version checks, staff permissions, the queue lock and activity records. Merging companies with colliding contact emails is rejected atomically until staff resolve the duplicate contacts.

Use **Invoices → Ready to invoice**, or **Prepare hourly invoice** on a project. Select 1–50 completed billable time entries, the connected billing client, a positive USD hourly rate, public descriptions and an optional invoice note. Review the exact Decimal-calculated lines and total before confirmation. Running timers, removed/nonbillable time and time already reserved are excluded. Job-linked time must belong to the selected company. Accepted fixed-price scopes require explicit acknowledgement that these hours are separately billable additions. Imported/manual FreshBooks invoices cannot automatically identify which local hours were billed; check those before selecting time.

Confirmation creates one durable local invoice request and reservations under a database uniqueness constraint. An expiring signed preview binds the actor, time versions, billing connection, client version and scope. Repeat confirmation returns the original request. The existing background billing job creates an unsent FreshBooks draft; it does not mark it sent, email it or collect payment. Review taxes, due dates, payment settings and the invoice in FreshBooks before sending. No new scopes, service or hosting charge are required.

Reserved time cannot be edited or removed. Local queued or definitively rejected requests with no provider ID may be released after confirmation, making those hours available again while retaining history. Creating, uncertain or linked invoices retain their reservation. Uncertain creates require finding the exact existing provider marker/customer/currency/amount; they are never automatically recreated. Existing accepted-proposal invoices still work independently. Hourly invoices appear in the invoices overview and project reports without inventing proposals.

Apply additive migration **0031_timebillingbatch_timebillingline_timebillingrate_and_more** before serving the feature. It adds billing records and optional hourly invoice source fields, scopes contact-email uniqueness and adds explicit contact linkage. Existing queue requests, files, time, proposals and imported financial history remain intact. All screens require existing staff permissions, CSRF protection and no-store headers. Overview queries defer invoice payloads and reviewed snapshots to keep memory bounded.
