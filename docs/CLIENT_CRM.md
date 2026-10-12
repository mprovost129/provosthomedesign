# Client CRM

Staff use `/clients/` alongside `/work-queue/`, with the same Django staff login and `workqueue.view_workitem` permission. Client/contact edits and connections require `workqueue.change_workitem`. Public email verification does not grant CRM access. Private pages are marked noindex and no-store, and use the website header/footer without analytics.

## Everyday use

- Click **Clients** at the top of the queue, or a client's name on a request. These queue links open another tab to preserve unsaved queue edits.
- Search by name, company, email, phone, request number, project name or submitted project street. Show all clients, clients with active work or repeat submissions.
- A client record holds contacts, current billing details and private notes. Its history includes independent requests, submitted answers, attachment links, completed deliveries, milestone/information-request email states and appointments linked to its projects. Appointments without a project can also match a contact email and are explicitly labeled as unassigned.
- Editing CRM details does not rewrite intake answers or change existing email recipients. Historical submitted contact details remain visible on each request.
- Add another contact/email to the client record to recognize future submissions from that address. Adding a CRM contact grants no project or appointment access. Manage those permissions separately in the queue.

## Matching and duplicate connections

The matcher trims and lowercases email addresses. It does not remove Gmail dots or plus suffixes. Email uniqueness is per client, so one person can have separate contact records at different companies. Repeat submissions reuse an unambiguous contact matching both email and company context while retaining independent queue entries and positions. Submitted changes do not overwrite the current profile.

Different emails remain separate contacts. Public intake groups contacts under an existing company only when its exact normalized company name identifies one active company. Name and phone similarities are suggestions only. `Homeowner`, blank and equivalent individual labels never group unrelated clients. Project names and addresses are not client identity keys. Shared-mailbox submissions in the same company use one contact; their original names remain on each request. Staff can explicitly link a person across companies without combining company records or granting access.

To connect duplicate records, open the record to archive, choose the client to keep and **Preview connection**. Review the contacts, work count and FreshBooks connections, then **Confirm connection**. The kept profile/notes remain unchanged. The source profile/notes remain available in the client change history. Contacts, follow-ups and unambiguous billing connections move to the kept client. Files, submission snapshots, project relationships, existing invoice records, queue order and public access grants are unchanged. If both profiles have different FreshBooks customers in the same business, the merge is blocked for billing review; neither connection is silently replaced.

**Undo last connection** restores the source contacts, follow-ups and moved billing connections while neither account nor its billing connections have changed. Later changes block a stale undo rather than guessing where records should belong. Older merge events without billing snapshots cannot be automatically reversed when either profile has a billing connection. Staff edits, billing links and merges are audited and use optimistic versions plus the queue transaction lock. For a client merged before this safeguard, review and reconnect its FreshBooks customer from the kept profile; historical invoices are preserved.

## Migration and operations

Migrations 0017–0018 add CRM models and populate contacts from existing work in received order. Missing or invalid historical emails receive separate contacts; source data is retained. Later nonblank historical details can fill an initially empty profile field during backfill, without replacing existing supplied values. Backfill is idempotent.

New requests attach through `create_work` within the existing transaction. The existing scheduled job also runs `sync_crm_clients` to catch legacy imports or submissions made during the deployment transition. It only scans unlinked requests and never sends emails or changes access.

The existing database backup/recovery setup covers CRM data. Rolling back application code does not require reversing the data migration. Do not remove CRM tables to roll back a UI release: that would remove client edits and history.

Initial CRM release validation: 281 workqueue tests passed on disposable local PostgreSQL, including concurrent matching, migration preservation, permissions, CSRF, escaping, stale writes, merge preview/undo, queue ordering, intake, email and booking regression coverage. Local browser verification used fictional records and memory-only email settings.

Ongoing validation runs in `.github/workflows/crm-tests.yml` for pull requests and
main-branch updates. It uses PostgreSQL 17 and isolated settings that never load
production `.env` values or send external email, checks for missing migrations,
runs the repository's Django tests (including PostgreSQL concurrency tests), and
runs the intake JavaScript and website-monitor checks. Regression tests in
`workqueue/test_crm_stabilization.py` cover billing transfer/undo, changed previews,
conflicting connections, invoice preparation after merging and company-scoped
appointment/notification history. The supported Django 5.2 security patch is
pinned in both requirements files; deployment must install the updated pins.
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

## Correcting a contact email

Use Edit contact on the client page to add, replace or clear that contact's email.
Emails are trimmed, lowercased and unique within each client; conflicts are rejected
with guidance to use the existing contact at that client. The same email can appear
at another company without merging either company. Changes record
the old and new email in private CRM history. Existing job contact snapshots,
submission ownership, queued notices and public access grants are not rewritten.
New manual jobs use the corrected CRM email; future public submissions with that
email attach to this contact. Older open forms that omit email preserve its value.

## Copy, delete and restore contacts

Copy opens a prefilled form under the same client, with an empty email to avoid
reusing another contact's unique address. It copies contact details only and
requires at least a name, email or phone; jobs, files and public access are not
copied. Add contact now also accepts a name or phone without an email.

Delete removes a contact from active selection and the directory, releasing the
email for future intake. The contact remains linked to its historical jobs.
Deleted contacts stay in the private client record with a Restore button. When
restoring, the original email is restored only if it is available; staff can
explicitly uncheck Restore original email when another contact has claimed it.
Existing work email snapshots, submission ownership and access grants stay intact.
Every change is audited and checks the client version under the queue lock.
