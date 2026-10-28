# FreshBooks read-only mirror

FreshBooks remains the source for real billing. The staff-only mirror at
`/work-projects/billing/mirror/` copies clients, projects, invoices and recorded
payments. Estimates are optional and require `user:estimates:read` and an owner
opt-in. This feature sends no emails and creates no provider billing records,
queue requests, native financial entries, project numbers or Dropbox folders.

## Operating the mirror

Open the mirror from Clients, Projects, Invoices or the FreshBooks connection
page. An owner can enable or pause background checks and select estimates.
The existing `sync_freshbooks_billing` scheduled command processes durable,
bounded pages. A completed resource scan becomes eligible again after five
minutes; larger scans resume over subsequent runs. Sync now schedules work
without holding a browser request open. It has a one-minute cooldown.

The coverage table shows progress, the last complete scan and sanitized errors.
Individual records can appear before a full scan finishes. This is periodic
mirroring, not instantaneous synchronization or an independent backup.

## Identity and CRM

Records are unique by FreshBooks account, resource kind and provider ID. Repeat
scans update records rather than create duplicates. Clients first reuse an
explicit provider identity, then a unique exact name/company and email match.
Ambiguous matches require staff review. Companies sharing an owner or email
are not merged. Staff can explicitly link a source client to an existing CRM
client after checking its identity. The form checks record versions.

Managed CRM fields use a three-way comparison: a later FreshBooks change can
replace the last automatically copied value, but a differing local edit is
retained and flagged. Private notes, client pricing category, booking approval
and archived local contacts are preserved. New imported contacts do not receive
booking access or invitations. Projects remain source records unless already
explicitly connected to an internal project.

## Financial integrity

Invoice/estimate totals, source lines, paid and outstanding values retain their
source currency and precision. Totals are not recomputed from rounded lines.
Payments link through source invoice IDs. Credit applications are labelled and
are never added as a second cash collection. Mirrored amounts are not summed
into native test billing or CSV history. The interface provides source views,
not another independently calculated revenue ledger. Processor fees remain
unknown unless separately verified from the payment provider.

Earlier source revisions are preserved with checksums. A failed or partial
scan retains the last verified data; missing records are flagged only after a
complete successful scan. Missing does not mean paid, deleted or zero balance.
The allowlist excludes payment notes, card data and processor references.

## Recovery and access

Jobs have expiring leases, bounded pages, a five-minute successful rescan delay
and a fifteen-minute failure retry delay. Pausing, losing read permissions or
changing the selected account stops application of in-flight results. Reconnect
the same business and use Sync now after correcting a connection problem.
Do not attach existing records to a different business identity.

All mirror views require staff authentication and use private, no-store
responses. Settings require owner access; identity changes require the existing
work-item change permission. Every provider request made by the mirror is GET.
Existing FreshBooks write permissions support other separately initiated app
features and are not exercised by the mirror.

## Validation

The focused tests cover repeated imports, shared emails, local edits, archives,
pagination changes, wrong accounts, invalid money, currency preservation,
missing records, lease recovery, interrupted scans, revisions, explicit links,
read-only OAuth scope expansion, CSRF, permissions and no financial side effects.
