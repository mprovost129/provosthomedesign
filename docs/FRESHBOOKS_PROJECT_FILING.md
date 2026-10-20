# FreshBooks projects from approved filing

Client submissions still enter the queue automatically. Staff's filing preview can request **Dropbox only**, **Create or reuse FreshBooks project**, or **Connect an existing FreshBooks project**. Once project permissions are connected, creation is selected initially; pricing defaults to **Fixed price**, with **Hourly** as the other choice. An actual positive amount/rate must be supplied for a new project; no guessed prices, placeholder zero amounts or estimates are sent. Updates reuse the existing connection and pricing. Already filed Dropbox projects can request their FreshBooks connection without re-filing files or allocating a new number.

The signed 15-minute filing preview freezes the business, client profile, existing client match, project title, billing type and price/rate along with Dropbox's exact destination/file manifest. Approval validates them again, atomically reserves the folder number and records one FreshBooks outbox per website project. Old previews and Dropbox-only approvals do not create FreshBooks records. No existing approvals are backfilled automatically.

## Connection

The existing app adds `user:projects:read`, `user:projects:write`, and `user:clients:write`. Existing scopes for billing remain. An owner must reauthorize and select the billing business; project endpoints use its numeric **business ID**, distinct from the accounting account ID used for clients/invoices. Consented scopes are saved, and refresh responses without a scope retain the grant. Old grants remain usable for billing but cannot create projects. No new credentials, hosting service or scheduled charge is required. Owner confirmation is required when adding the new provider permissions.

## Client and project identity

Use an explicit CRM/FreshBooks billing-client link first. Otherwise check all paginated email and name matches, including archived/deleted states. A unique exact name/company plus email can be linked; ambiguous or different-company matches require the existing client Billing connection UI. No merging by shared email. A genuinely absent client is created from the selected billing/primary contact, company and billing address; internal notes, files and other contacts are excluded. Multiple billing contacts or incomplete billing email require correction first. Existing provider data is never overwritten.

The FreshBooks title uses the approved Dropbox folder name, including the YYMM## business number. Linking an existing provider project requires its numeric ID and matching billing client. New creations check project names/numbers before writing. The website records one provider project ID per website project and prevents another local project claiming the same provider project. Request IDs, queue positions, S3 files, client portal permissions and Dropbox numbering stay independent.

## Worker, failures and recovery

The current minute job runs `process_freshbooks_projects` after `process_dropbox_filings`. It processes one finite client/project step per run. The Dropbox project folder must be connected before a FreshBooks write; copying remaining source documents can continue independently. Durable working intents serialize project provisioning across overlapping workers. Each client POST has its own unique client/account intent, and every create intent is committed before its remote write. Read pagination is bounded; errors never log provider bodies/tokens.

Known rejected writes go to **FreshBooks needs attention** and can be retried after correction. Timeouts, unverifiable responses and abandoned working intents go to **Check FreshBooks before retrying**. They cannot be automatically retried. **Check FreshBooks** performs read-only marker reconciliation, including the approved client, title and price/rate. If the result is not uniquely verified, staff can inspect FreshBooks and explicitly connect an existing project with the exact approved name/client. No recovery operation deletes or recreates a provider record. A failure in either destination cannot consume another project number or remove originals.

The filing page and queue row show FreshBooks status separately from Dropbox status. No invoice, invitation, email, payment, time-entry or attachment-upload action is performed by project provisioning. Billing remains separately approved.

## Release checks

- Apply additive migration `0042_freshbooks_project_filing` before new web/worker code serves traffic.
- Reauthorize with the expanded scopes and reselect the original business.
- Verify one explicitly approved fictional project against an unnumbered test Dropbox folder and a test/owner billing client. Do not consume the next real project number or send invoices/emails.
- A rollback can leave the additive tables in place; do not reverse the migration once approvals exist. Existing website/Dropbox flows continue without project provisioning when using the previous code.

Provider references: https://www.freshbooks.com/api/project , https://www.freshbooks.com/api/clients , https://www.freshbooks.com/api/scopes , https://www.freshbooks.com/api/parameters .
