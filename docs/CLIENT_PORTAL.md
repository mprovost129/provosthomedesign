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

## Plan review and revision requests

Open **Projects → project → Client sharing → Approve or update a contact’s access**. Select the contact, check **Allow plan comments and revision requests**, review its other permissions, and approve. Existing memberships remain view-only after migration 0033. This permission is separate from financial access. Upload a fixed PDF plan under Published documents; external Drive links and invoices do not offer Review plan.

Clients open the project link, verify their approved email, choose **Review plan**, and start or resume a draft. They can place up to 100 numbered comments, select pages and zoom, and write general notes. Autosave is serialized and version checked. Normalized coordinates refer to the original PDF.js viewport, including its crop and intrinsic rotation; zoom and phone size do not move pins. Render one page at a time, capped at four million canvas pixels, with a 500-page ceiling and a general-notes/native-PDF fallback. PDF.js 6.4.299 is self-hosted, integrity checked from the official npm archive, with its license and source metadata retained. No third-party document renderer, CDN, provider scopes, or new resources are needed.

**Submit revision request** freezes the exact release, author/client identity, comments, geometry and original plan title. A queue lock and review identity ensure repeated or concurrent submissions produce exactly one connected update WorkItem and one pair of durable receipt emails. The approved contact is used directly; matching a company name or a shared email never reassigns this submission to another CRM client. The request gets its own reference and position. Its saved record displays current status and queue position; the original submission position remains immutable. Reads and draft saves neither allocate a job nor send mail. Further changes require a new review; old pins do not move to a replacement plan.

The queue has a **Plan review** link and New flag. Staff may also use **Projects → project → Plan reviews** to read submissions, open the drawing, download the original PDF or a small **review report PDF**, and mark the review read. Marking read does not finish the job or send a message. Drafts remain private to their author, including from staff. The report contains the frozen notes and their page/location references; it is **not a flattened annotated copy of the original drawing**. Keep the report and original PDF together for offline review.

Every client read/download rechecks the active project membership and document visibility. Editing/submission also require review permission, the original grant epoch, an active unchanged CRM contact/email/company, and an unmerged client. CRM changes or reapproval make older drafts read-only rather than transferring them silently. Revocation, withdrawing a document, or changing the membership’s billing client cannot expose another client’s draft or review. Staff can retain and read submitted history after withdrawal/revocation. CSRF applies to all writes. Notes render as text, and private pages/downloads are no-store and noindex.

Migration 0033 adds the opt-in review flag and protected review table with a partial unique draft constraint and paired submission/work-item constraint. There is no automatic grant, data import, or publication. Existing receipt jobs deliver review notifications through the same outbox and provider-reconciliation safeguards. Rollback must keep additive review tables and submitted records; do not reverse migration 0033 after real reviews exist.

## Drawing tools and marked plan exports

Reviewers can choose **Note pin**, **Arrow**, **Box**, or **Freehand**. Activate Add a note/Draw, then tap a location or drag on the plan. Each mark has a numbered comment. Freehand strokes retain at most 300 points each; all marks together retain at most 3,000 points, within the existing 100-comment limit. Undo restores the last added/removed mark within the current editing session; after reopening a saved draft, use Remove on the particular note. Existing pin payloads remain valid. Autosave, stale-draft protection, explicit submit and immutable version/author identity apply equally to drawings.

After submission, either the author or staff may choose **Prepare marked plan (PDF)** on the review page. An idempotent OneToOne export job runs in the existing minute worker, at most one export per scheduled run, after calendar, CRM, email and cleanup tasks. Refresh the review after it is ready and choose **Download marked plan (PDF)**. The output includes vector pins/arrows/boxes/strokes baked onto copies of the original pages, followed by a readable notes report. Original plan page numbering is retained. Original PDFs, frozen reviews, queue records, and receipt messages do not change when preparing/downloading a copy.

The copy uses the exact immutable source release, accounts for CropBox offsets, intrinsic 0/90/180/270 rotation and UserUnit, and rejects a review/source page-geometry mismatch rather than placing marks incorrectly. Existing drawing/layer settings are retained. Root scripts/embedded-file name trees and common page/annotation actions are excluded from the copy; this is not a general-purpose PDF sanitization service. Exports are identified as client review copies, not newly issued construction plans or approval of pricing.

The web process never parses the source PDF for export. A separate isolated Python subprocess uses pinned pypdf 6.20.1 and existing ReportLab, with no inherited database, mail or provider credentials. The parent imposes a 30-second wall deadline; Linux additionally limits the child to 192 MiB address space, 20 CPU seconds and 110 MiB output. Sources are streamed through temporary files, with a 100 MiB input/500-page ceiling. Encrypted, interactive-form/signed-form or overly complex plans may fail safely; the original and standalone review report remain available. Three attempts maximum per review, each retry requiring an explicit POST. Interrupted processing is marked failed after five minutes, never left permanently pending.

Migration 0034 adds the export job, lease token, status and private file reference. Copies use `intake/review-exports/` in the existing private bucket; no new storage permission, calendar scope, service or subscription is needed. Downloads recheck current project membership, frozen author/client identity and document visibility, even for cached exports. Staff may still read submitted review history after client revocation/withdrawal. No public or presigned download link is introduced, and generating a copy sends no new email or queue request. Do not reverse migration 0034 after exports exist; keep the additive records during application rollback.

## Later enhancements

Threaded replies and explicit carry-forward to replacement plans can extend the version-bound review foundation. Uploaded originals must remain immutable. A later Google Drive picker/import can use narrow `drive.file` access and copy a deliberately published PDF into private storage rather than depend on mutable Drive previews or revision retention.
