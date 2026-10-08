# Historical queue import and cutover

The website database is now the working queue. The original Google Sheet and its private, lossless export are retained as historical archives. Never commit a real snapshot, client information or connection credentials to GitHub.

## Live handover completed — October 7, 2026

The final frozen workbook matches the imported source exactly: 25 original requests, 9 active and 16 completed, source counter 27. All five linked Google forms are closed with existing responses retained; no registered responses remained unsettled. The old update form's public closed page was checked and directs clients to the website. The Apps Script handover flag now disables the old intake and background mutation handlers. The final snapshot's canonical import digest matches the retained production archive: `99fb6c20db89a0b8de6a54baebaeb788c06fe78a1b762f7eebf47c5a48184ff4`.

All three feature flags are enabled on both Render services, the scheduled worker is running, and the staff preview flag is disabled. The old Google appointment schedule was deleted with the owner's explicit approval. Its public page reports “Appointment not found”; the existing booked calendar appointment remained. Primary Calendar history and existing private Drive uploads were preserved.

Website navigation now includes Projects → Submit work or an update, Track my project, and Book an appointment, plus footer links and a homepage submission button. The Work queue link requires existing staff permission. Public routes are `/submit-work/`, `/track-work/`, and `/book-appointment/`; the staff route is `/work-queue/`.

Actual HTTPS browser checks created owner-only test requests PHD-00028 and PHD-00029. The new request accepted PDF and PNG uploads and multiple attachment categories; its receipt reported position 10. An email-verified update linked by exact project name without a supplied ID and received position 11. Completing the first test through the queue moved the update to position 10. Both test requests were then completed, returning the active queue to the original nine jobs. Their closed records remain as an audit trail; the reference allocator was intentionally not rewound and holds 29.

Both new-request emails arrived in the owner's Gmail inbox. The owner notice included contact details, billing address, selected categories and both filenames. Its client receipt's single-use sign-in link opened tracking, and the authorized private PDF download succeeded. Production delivery metadata confirmed all four new/update submission notifications were sent only to the owner test address.

A browser appointment was confirmed by the scheduled worker, then canceled through the client page. Google readback verified removal of both the meeting and its buffer. Ten disposable sample object keys and three test attachment records were removed under the approved test scope; the S3 console confirmed Objects (0). No original files or client work were removed.

Live checks identified and fixed two issues: corrupted PNG checksums now produce client validation errors, and queue mutations preserve filter values separately from edited request fields. Saving Completed now leaves remaining active work visible. Commit `ff1dad150804cfdd60fff398c5541cd437c670fc` is live as Render deployment `dep-db3f9vc9v7es73a8mk50`. The final queue suite passed 142 cases with four PostgreSQL-only concurrency cases skipped on SQLite; those cases passed in the earlier isolated PostgreSQL checkpoint. Desktop browser navigation and the full submission/tracking/booking flow were checked; mobile viewport emulation was unavailable in this browser session.

Day-to-day: review `/work-queue/`, change status and click Save status, and expand a request for details, files, internal notes or project connections. Completed work leaves Active work automatically and remains available under Closed work or All work. Do not resume old forms or import again after live app writes without a deliberate reconciliation plan.

The sections below record earlier rehearsal and pre-launch checkpoints; their disabled flags and suspended-worker descriptions are historical.

## Verified rehearsal — October 7, 2026

The inspected Sheet snapshot contains 25 numbered requests, 9 active and 16 completed. The Apps Script project setting `PHD_REQUEST_SEQUENCE` was independently read as 27. Preserve gaps and every original reference; new allocations must start above this counter, not above the number of imported rows. Queue order values are preserved rather than using the current Sheet sort order.

The owner explicitly approved connecting PHD-00027 to PHD-00001 (Lot 14 Achin Acres) and PHD-00016 to PHD-00002 (Lot 15 Achin Acres). Both pairs retain separate work items, statuses, numbers and positions. No other records are merged by guessed names, addresses or company relationships.

The isolated PostgreSQL rehearsal verified 25 requests, 29 associated submissions (including six preserved legacy-row captures), 19 original Drive files and one external link. Five submissions associated with removed/test records remain in the full source archive and are not promoted back into the queue. Blank historical contact/billing fields are retained; no addresses, phone numbers or companies are fabricated. Source timestamps are interpreted in the Sheet's Eastern timezone and stored as aware timestamps. Unknown statuses, duplicated IDs/orders, invalid supplied data and missing issued references stop the transaction.

All 204 tests passed on isolated local PostgreSQL, including queue/booking concurrency, import rollback/retry, confirmed project relationships and private original-file access. PostgreSQL testing identified and corrected a nullable-join appointment lock: only the appointment itself is locked while the booking singleton continues to serialize related changes. The download test consumes Django's streaming wrapper instead of manually closing the surrounding database test transaction.

## Command and controls

`python manage.py import_legacy_queue PRIVATE_SNAPSHOT.json --reference-high-water 27 --connections PRIVATE_CONNECTIONS.json`

The command defaults to a full validation/dry run and rolls back every database write. Add `--apply` only after checking the summary against the fresh source. All three queue/intake/booking feature flags must be disabled. Import writes and allocator changes are atomic under the same singleton queue lock used by normal app writes.

The import preserves original Sheet queue UUIDs and uses deterministic IDs for projects, submissions and attachments. Repeating the same snapshot does not create duplicates or notifications. A refreshed Sheet snapshot can reconcile changed statuses and append submissions before cutover, provided the app's imported records are unchanged. An app edit, missing source request/submission/attachment, unrelated live work or incompatible source data stops reconciliation instead of silently overwriting or deleting records. Each changed item has an import audit and database fingerprint.

Every accepted snapshot is retained in the private `LegacyQueueImport` database table, including all otherwise unmatched rows and original provider receipt metadata. The import does not create notification deliveries, sign-in tokens, project access grants or calendar events. An internal operator's verified Google email is never substituted for the project's contact email. Existing clients can verify their recorded contact email to see their own imported submissions and become eligible to book, just as new verified clients do. Verification does not grant project-wide access, authorize unknown emails or undo an explicit staff booking ban. Additional project-wide access and booking restrictions remain staff controls on the queue page.

Original uploads remain in their existing private Google Drive locations. Staff-only download routes open their validated Drive file IDs; Google retains its existing access checks. Original files are not copied, made public or exposed to clients through the app. Client tracking shows these files as retained in the original intake archive; new app uploads use private S3 and authorized application downloads. External links remain captured answers and are never fetched automatically.

## Final handover

1. Keep the existing Google forms authoritative while reviewing the imported queue and existing appointment capacity.
2. Before opening the new public intake, pause the old forms, wait for pending Apps Script submissions to finish, export a fresh workbook, reread the script allocator and reconcile the final snapshot. Check all counts, references, queue positions, statuses, connections, answers and file links. Retain both snapshots and the original Sheet.
3. Preserve existing Google appointments and account for them in the two-per-day limit. Restrict/retire the old booking path so it cannot bypass the app's access rules or limits.
4. Run the full HTTPS submission/upload/email/verification/tracking/booking checks with approved disposable test records. A provider-only check does not establish the complete browser workflow.
5. Enable the same feature flags on both Render processes, resume the scheduled job and update the website links as a coordinated cutover. Confirm a real client submission writes directly to the single queue, sends both notices and cannot be duplicated by retry.
6. If handover fails before clients switch, leave the app gated and restore Google form availability. After clients switch, reconcile any new app submissions and calendar reservations before rollback; retain the additive database tables and source archive.

## Production import verified

Commit `d9e51d5c8dbc813a7efdd1472a3c478b2174077a` was deployed successfully on Render as deployment `dep-db3dfkugekts73di6fug`. Migration 0010 added only the private source-archive table. The web shell confirmed zero existing work items, zero archives and all three feature flags disabled before importing.

Immediately before the import, the Google workbook was exported again and verified unchanged. The deployed importer validated and rolled back the dry run, applied the approved snapshot, and repeated the same import with zero changed requests. Production readback confirmed 25 work items (9 active, 16 completed), 29 associated submissions, 19 private original Drive file references and one external link. Five removed/unmatched test submissions are preserved in the source archive and were not reintroduced as work. Both owner-approved project pairs were checked by actual shared project and previous-request IDs.

Active references in order: PHD-00008, PHD-00009, PHD-00010, PHD-00014, PHD-00015, PHD-00024, PHD-00025, PHD-00026, PHD-00027. Global positions were verified 1–9. The allocator holds reference 27 and order 250. Notification deliveries, sign-in tokens, booking permissions and project-wide access counts were checked unchanged; the import sent no emails and created no calendar events.

The snapshot archive digest is `99fb6c20db89a0b8de6a54baebaeb788c06fe78a1b762f7eebf47c5a48184ff4`. Public features remain disabled and the scheduled job remains suspended. The working Google forms remain authoritative until final reconciliation/cutover. Staff browser review requires the owner's existing website login; no replacement credentials or access bypass were introduced. The cron job must run this deployed code revision before client launch.

A read-only check of the owner's primary Google Calendar found zero future busy events over the configured 60-day booking horizon, with timezone America/New_York. No existing appointments require capacity migration at this checkpoint. Recheck before final cutover; the old public Google booking route has not been retired.

## Staff preview and deployed workflow verification

The owner signed in through the existing Django admin. `WORK_QUEUE_STAFF_PREVIEW=True` now enables a read-only queue for existing staff with view permission. Public intake, queue and booking flags remain disabled. Preview rejects all queue POSTs, even for a superuser, and exposes no edit/access-management controls. Browser checks verified the nine active positions, all sixteen completed records, both approved project connections and an original private Drive document. The scheduled job remains suspended.

The controlled deployed-runtime rehearsal passed using the actual views, production CSRF/session middleware, PostgreSQL, private S3 and primary Google Calendar. It checked required-field validation, PDF/PNG direct upload with the site's CSP and bucket CORS, sealing, authorized download, new/update request linking, separate initial positions, current tracking, single-use verification, durable email composition/retries, duplicate prevention, and appointment confirmation/cancellation with its buffer. Eight email messages were captured in memory; no real mail was sent during this rehearsal. Actual Gmail inbox delivery was confirmed by the owner separately.

This check found a production browser-policy mismatch: S3's default upload endpoint differed from the regional host in connect-src. Commit `391c758` fixes private storage to use a regional virtual-hosted endpoint without expanding browser permissions or changing public media storage. After the fix, 185 queue and existing website page tests passed on isolated PostgreSQL. The broader 207-test checkpoint preceded this endpoint correction.

All rehearsal database records and reference/order allocations rolled back. The calendar provider verified removal of both owned sample events; Google may retain canceled-event tombstones. S3 accepted deletion of four generated sample objects, and the AWS console subsequently confirmed Objects (0). The app account deliberately lacks bucket-list permissions, so HEAD of an absent object can return 403; that response alone is not proof that a file remains.

This was a deployed-runtime rehearsal plus staff browser review, not a complete public browser submission test. Public HTTPS browser intake and final source reconciliation still require the coordinated cutover above. Do not advertise the new intake link or retire the source before completing that handover.
