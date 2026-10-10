# Client CRM

Staff use `/clients/` alongside `/work-queue/`, with the same Django staff login and `workqueue.view_workitem` permission. Client/contact edits and connections require `workqueue.change_workitem`. Public email verification does not grant CRM access. Private pages are marked noindex and no-store, and use the website header/footer without analytics.

## Everyday use

- Click **Clients** at the top of the queue, or a client's name on a request. These queue links open another tab to preserve unsaved queue edits.
- Search by name, company, email, phone, request number, project name or submitted project street. Show all clients, clients with active work or repeat submissions.
- A client record holds contacts, current billing details and private notes. Its history includes independent requests, submitted answers, attachment links, completed deliveries, milestone/information-request email states and appointments for its contact emails.
- Editing CRM details does not rewrite intake answers or change existing email recipients. Historical submitted contact details remain visible on each request.
- Add another contact/email to the client record to recognize future submissions from that address. Adding a CRM contact grants no project or appointment access. Manage those permissions separately in the queue.

## Matching and duplicate connections

The matcher trims and lowercases email addresses. It does not remove Gmail dots or plus suffixes. Each valid normalized email has one contact record, linked to one client. Same-email submissions reuse that contact while retaining independent queue entries and positions. Submitted changes do not overwrite the current profile.

Different emails remain separate by default. Exact normalized name, phone or meaningful company matches are suggestions only. `Homeowner`, blank and equivalent individual labels never group unrelated clients. Project names and addresses are not client identity keys. Submissions from a shared mailbox appear under one contact; their original names remain on each request.

To connect duplicate records, open the record to archive, choose the client to keep and **Preview connection**. Review the contacts and work count, then **Confirm connection**. The kept profile/notes remain unchanged. The source profile/notes remain available in the client change history. The merge moves internal contact ownership only: files, submission snapshots, project relationships, queue order and public access grants are unchanged.

**Undo last connection** restores the source contacts while neither account has changed. Later changes block a stale undo rather than guessing where contacts should belong. Staff edits and merges are audited and use optimistic versions plus the queue transaction lock.

## Migration and operations

Migrations 0017–0018 add CRM models and populate contacts from existing work in received order. Missing or invalid historical emails receive separate contacts; source data is retained. Later nonblank historical details can fill an initially empty profile field during backfill, without replacing existing supplied values. Backfill is idempotent.

New requests attach through `create_work` within the existing transaction. The existing scheduled job also runs `sync_crm_clients` to catch legacy imports or submissions made during the deployment transition. It only scans unlinked requests and never sends emails or changes access.

The existing database backup/recovery setup covers CRM data. Rolling back application code does not require reversing the data migration. Do not remove CRM tables to roll back a UI release: that would remove client edits and history.

Validation: 281 workqueue tests passed on disposable local PostgreSQL, including concurrent matching, migration preservation, permissions, CSRF, escaping, stale writes, merge preview/undo, queue ordering, intake, email and booking regression coverage. Local browser verification used fictional records and memory-only email settings.
# Manual entries and project history

Staff can use **Clients → Add client** to create a CRM entry without a submission.
Only client name and type are required; unknown email/phone can remain blank.
An email already recorded opens its existing client without overwriting it. A
session-bound token prevents duplicate phone-only clients on a retried save.

Use **Add work** on a client's page to record a phone/email job. Select a contact,
create a named project or choose one already belonging to that client, describe
the work, and optionally upload up to 10 private files (100 MB each). Updates to
existing projects retain separate request numbers and queue positions. Contact
and billing snapshots come from the CRM; original submissions stay unchanged.
Manual saves do not send automatic receipt emails or establish project-wide
viewing grants. The recorded email can access its own submission after normal
email verification, as with existing staff entries.

Client pages list projects across all their contacts and include both website
submissions and staff entries. **View jobs** filters that client's history to a
project. Unlinked requests remain visible. Project counts span the full history,
including completed jobs, even when the job list is paginated or filtered.

Creation requires the existing staff permissions: `view_workitem` plus
`change_workitem` for clients, or `add_workitem` for work. These actions are unavailable
in staff preview mode. Contact/project ownership, optimistic client versions,
file nonce/session/expiry and idempotency are checked under the queue lock.
