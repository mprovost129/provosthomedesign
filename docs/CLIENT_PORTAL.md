# Client project portal

The client portal lives at `/client-portal/`. Clients verify their approved email with a one-use link, choose a project, and see the documents and financial records shared for that project. A direct project URL also works: an unauthenticated visitor signs in before any project details are shown.

## Staff workflow

1. Open **Projects → project → Client sharing**, or **Clients → client → Projects → Client sharing**.
2. If the client is not already connected through work, use **Link another client to this project**. This adds a CRM relationship without creating a job, moving the work queue, or granting anyone access. Explicit connections can also be selected when preparing a project proposal.
3. Under **Approve or update a contact’s access**, select the client/contact and review the exact email. Enable financial access only when that person should see that client's sent proposals, invoice records, and invoice documents. Approve the checkbox and save.
4. Copy the direct project link and send it using your usual communication workflow. Saving access does not send an invitation. A client requesting sign-in receives a link through the existing durable email outbox and background worker.
5. Publish a file or Google Drive link under **Publish a document version**. Choose an existing document to append its next version. Its category and billing audience are retained. Use a separate new document when the audience must differ.

Each person sees every project explicitly approved for their email. Multiple contacts can access one project. Each email has one selected billing client per project; approving that address again replaces its selected client and financial permissions for that project. Other project permissions remain unchanged.

## Files and version history

Files use the existing private upload pipeline: session-bound reservations, validated content, sealed storage keys, and a committed `attached` state. Publish one file per version, up to the existing 100 MB limit. Plans and invoices must be PDFs; general documents support the existing PDF/image/DOCX/XLSX formats. PDFs can be viewed in the browser or downloaded. Other formats download as attachments.

`PortalDocument` identifies a logical document and its audience. `PortalDocumentVersion` records the ordered release, original filename, byte count, storage key, publication note, time, and actor. New versions append; there is no editing or replacement route for a published version. Every download, including older versions, rechecks current project membership, document visibility, and the financial audience. Withdrawing a document hides all its versions; restoring it exposes the existing history again. It cannot recall files someone already downloaded.

Google Drive/Docs links are supported as external documents. They open through an explanatory portal page, with no automatic download, embedding, OAuth, or Google permission changes. Staff must restrict Google sharing to the intended recipients themselves. Google may require the client to sign in. These links are mutable: the portal preserves the link and publication history, not historical Google file contents. Upload PDFs when a fixed release and portal-controlled download are required. Invoice documents must be uploaded privately, not linked to Drive.

Existing intake attachments and completed deliveries are not automatically republished. Their existing tracking permissions remain separate.

## Proposals and invoices

Financial access is constrained by both project and the exact approved billing-client record. Clients see immutable sent proposal revisions, including their recorded status and PDF copy. Working drafts, project planning notes, staff correspondence, time-entry notes and provider payloads stay private. Acceptance and revision responses continue through the existing private proposal-email workflow; the portal copy does not create a second acceptance flow.

FreshBooks invoices appear after they are linked and have a saved sent/payment status; drafts, uncertain creations and unrecognized states are excluded. Imported invoice history must be applied, explicitly attached to this project and client, and not removed. Matching imported/live invoice numbers are deduplicated. The UI distinguishes stored balances from historical exported line totals and shows the last verification time. Portal page reads do not call FreshBooks, create invoices, collect payments, or send email. Upload the official invoice PDF under **Invoice documents** when a downloadable copy is needed. Payments continue through the existing FreshBooks client workflow.

## Access and integrity

- `ClientProject` is an explicit CRM relationship. `PortalMember` is a separate access grant. Neither matching an email, linking a client, holding an old tracking grant, nor knowing a UUID grants portal access.
- Portal identity reuses the existing hashed one-use email tokens (24-hour expiry), verified sessions (seven-day expiry), session rotation, CAPTCHA, CSRF checks, and shared IP/email rate limits. Unknown and rate-limited addresses receive the same generic UI response.
- Grants freeze the approved email and billing client. CRM edits, archives, or merges do not move access to a new email or expand the financial audience. Staff see a notice if the source contact changes and must review/reapprove or revoke access as appropriate.
- Revocation/downgrading is effective on the next portal request. It does not revoke independent work-tracking access or Google Drive permissions. Portal sign-out also clears the local proposal-invitation session.
- Sharing mutations require active staff with `workqueue.change_workitem`, CSRF protection, and explicit approval for grants/publication. They run under the queue lock with a project-sharing version, preventing stale forms or simultaneous actions from silently replacing approvals. Project events record each change.
- Private responses use `no-store`, no-index headers, same-origin referrers for form compatibility, and safe file response headers. External links suppress referrers. No analytics are embedded in the private page base.

## Deployment

Migration 0032 adds portal relationships, document tables, constraints, and the email-token destination. It grants no existing contact access and republishes no existing documents. Existing queue/client feature controls remain in effect. The normal web deployment applies migrations; the background worker uses the existing email outbox. No new credentials, Google scopes, storage buckets, or paid services are required for this phase.

Rollback: restore the preceding application version and retain the additive tables. Do not reverse the migration after clients have used the portal without first exporting and reviewing its grants and publication history.

## Next phase

Visual plan markups and submitted revision requests are not included in this first portal release. Build those on immutable PDF document-version IDs, with PDF page coordinates, author identity, autosaved drafts, explicit submission, and an idempotent revision-request link to the work queue. A later Google Drive picker/import can use narrow `drive.file` access and copy a deliberately published PDF into private storage rather than depend on mutable Drive previews or revision retention.
