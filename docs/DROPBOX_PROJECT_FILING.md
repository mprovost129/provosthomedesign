# Approved project filing to Dropbox

Client submissions still enter the existing queue immediately. Filing is a separate, explicit staff approval reached from **File project documents** on each queue row. The review opens in another tab so unsaved queue edits are preserved. No second queue, changed request ID, queue reorder, automatic email, or public folder share is introduced.

## Numbering and destinations

The destination is the cloud Dropbox `/Projects` folder corresponding to `C:\Users\mprov\Dropbox\Projects`. Numbers use Eastern time **at approval**, `YYMM##`, restarting at 01 each month. The owner explicitly reserved October 2026 numbers through **261007** even if a local/index listing omits them. Next October number is **261008**. November starts at **261101**, January 2027 at **270101**. Gaps and deleted numbers are not reused. At 99 the system stops for a deliberate numbering decision.

New folder preview: `261008 - Project name - Address`. The preview expires after 15 minutes and is bound to the staff member, current request version, project, exact file manifest, Dropbox account/root, month, and destination. Approval checks the current remote Projects listing again. Concurrent approvals serialize through the connection and queue locks. If the number or request changed, staff must preview again.

An existing folder is selected from a private index of Projects' immediate child folders, searchable by project number, name, or address. A project has one destination; the same Dropbox folder cannot be assigned to two different website projects. Staff should first use queue Edit to correct an existing project association if needed. Filing does not grant any ProjectAccess or portal membership. Updates keep their separate queue entries and reuse the connected folder without allocating another number.

Each submission is copied into `Client Uploads/YYYY-MM-DD PHD-000xx submission-id/`. Filenames include a stable attachment ID plus the original filename. This preserves identical filenames across submissions and never overwrites working plans. The six-digit business project number remains separate from receipt/tracking IDs.

## Storage and transfer safety

**Phase one retains all S3 originals.** The portal, receipts, plan reviews, private downloads, and existing storage permissions continue to use S3. The integration has no delete/move/share endpoint and never exposes internal Dropbox working files to a client. Existing S3 originals are not automatically purged, including after verified copying. A later storage-provider change is needed before S3 can safely become temporary.

Only approved submissions have transfer records. The existing scheduled worker processes one file (up to the existing 100 MB per-file limit) per run, after calendar/email/cleanup tasks. Ten files may take ten normal minute runs plus provider delays. A database connection lock with skip-locked handling prevents overlapping transfer workers. Dropbox calls have bounded timeouts. Filing statuses and errors are visible in the queue review.

Transfers stream through a bounded-memory temporary file, check source size, compute Dropbox's 4 MiB block content hash, and compare the Dropbox result's size/hash/path. Deterministic destinations reconcile an interrupted upload without overwriting or uploading another copy. Different content at an existing name is held for review. A lost **project folder creation** acknowledgement is never silently adopted: refresh the folder index, inspect the exact approved name and number, and explicitly connect that folder to resume. A failed transfer preserves its reserved number and source originals.

Legacy Drive-only attachments must be imported before filing; they are not silently omitted. URLs/external document links in answers are not downloaded or executed. Initially this feature files submitted `Attachment` originals, not arbitrary desktop working files or completed-delivery exports.

## Connection and deployment

Create an OAuth Dropbox scoped **Full Dropbox** app to access the owner's pre-existing Projects folder. This content access is broader than a folder-specific grant: application code enforces Projects, but the Dropbox token itself is not restricted to that path. Use a dedicated identity/folder-sharing design instead if a provider-enforced account boundary is required. There are no team-administration scopes.

Requested scopes:

- `account_info.read` to identify the authorized account and namespace.
- `files.metadata.read` to index and verify the selected folders/files.
- `files.content.write` to create folders and upload approved files.

No `sharing.*`, `files.content.read`, or permanent-deletion endpoint is used. Note that Dropbox's content-write scope can authorize other write operations; the application endpoint allowlist is an additional code control, not a narrower Dropbox scope.

Set on both the web service and existing scheduled job (no extra paid service required):

- `DROPBOX_APP_KEY`
- `DROPBOX_APP_SECRET`
- `DROPBOX_TOKEN_ENCRYPTION_KEY`: a newly generated Fernet key, stored privately, never committed or printed. Retain it for recovery; changing it requires reconnecting.

Register the exact callback `https://www.provosthomedesign.com/work-queue/dropbox/callback/`.

Only a superuser with queue edit access can connect, reconnect, refresh the account index, or pause filing. OAuth uses short-lived, session/actor-bound state, PKCE S256, offline access, and encrypted tokens. Refreshes serialize across processes. Reconnecting cannot switch an existing account/root silently. Root namespace is retained for business Dropbox accounts. Root identity/path and destination containment are rechecked before writing; moved/outside-root folders stop filing.

1. Apply migration `0041_dropbox_project_filing` and deploy the tested code to web and worker. With no connection, transfers remain inactive.
2. Configure credentials privately in Render and authorize the intended Dropbox account via **Work queue → Dropbox filing**. Granting new Dropbox access requires the owner's action-time confirmation.
3. Check the displayed account, Projects root, index, and highest number. A read-only index refresh does not create folders or files.
4. Test with a specifically approved disposable project/sample document before the first real filing. Verify the folder and hash plus the unchanged source/portal; do not send test emails or delete sample files without authorization.
5. Begin approving real queue submissions. The app records who approved/retried/reconciled each filing.

Recovery: Pause filing immediately if the destination is wrong. Already filed documents are not removed. Inspect incomplete files in the private review, correct/reconnect the provider or explicitly reconcile an exact folder, and retry the same filing. Database/S3 backups and Dropbox recovery remain independent. Additive tables can remain during an app rollback; do not reverse the migration after approvals exist.

References: [Dropbox OAuth](https://docs.dropboxapi.com/dropbox-api/docs/oauth), [upload API](https://docs.dropboxapi.com/dropbox-api/api-reference/user-endpoints/files/upload).
