# Invoice follow-ups

Open **Invoices → Billing test area → Invoice follow-ups**, or **Billing emails → Open invoice follow-ups**. Each test invoice also has an **Invoice follow-up / reminder controls** link.

This release is an owner-only sandbox. All reminders go to the configured owner inbox, never the invoice's client. FreshBooks remains responsible for real invoices and collections. Nothing is added to real client balances, public tracking, revenue reports or client portals. There is no live-mode switch, new provider scope, credential, service or recurring charge.

## Review and controls

The workspace lists overdue test invoices with positive remaining balances, searchable by invoice, frozen client name or project. Voids, payments, credits, reversals, refunds, returns and recoveries are reflected in the amount. The query uses correlated signed ledger totals to avoid multiplying entries through related joins; the authoritative balance is checked again during review and queueing.

The owner can pause an individual invoice, snooze it until a future date, or review a manual reminder. Pausing or snoozing cancels that invoice's waiting reminders, including manual copies. Removing a pause does not restart canceled stages automatically. A known-unsent canceled/failed stage may be explicitly reviewed again. Unknown delivery outcomes must be resolved by checking the owner inbox, not retried blindly. Normal staff with workspace view permission can inspect the pages but cannot change these controls or send mail.

Each manual review is signed for fifteen minutes and binds the owner, invoice version, remaining balance, stage, original reviewed delivery and current reminder/outbox state. Replayed reviews are idempotent. A changed invoice, recipient or delivery state requires a new review. Mutations use normal CSRF protection and the staff-preview write guard.

## Automatic reminders

Automation defaults **off** and is independent of the payment receipt email setting. The owner may enable or pause it. Enabling records a new activation timestamp. Only invoices due on or after that local date are eligible; already-overdue invoices are not backfilled. An eligible invoice must have a previously sent, reviewed owner-only invoice copy. A revoked copy is not sufficient.

The fixed schedule is 1, 7 and 14 days overdue. Manual and automatic reminders share one durable slot for each invoice/stage. Only the latest eligible stage is considered; a delayed worker does not send missed stages in a burst. Automatic stages are at least forty-eight hours apart, measured against preparation/review updates and actual send attempts/delivery, including delayed mail. Automatic scheduling never retries a failed, canceled or uncertain stage.

The existing minute job queues reminders through the existing Gmail outbox. The bounded candidate query excludes completed slots, paused/snoozed invoices, recent follow-up and unresolved payment/reminder outcomes. Payment checkout, pending ACH, refund requests and payment-adjustment issues hold follow-up. The database unique constraint and shared QueueState lock serialize concurrent scheduler and manual actions.

Turning automation off cancels waiting automatic reminders but preserves explicitly reviewed manual copies. It cannot recall mail already sending. Re-enabling starts another future-only eligibility window. Per-invoice controls apply to both kinds of reminder.

## Message and attachment

Each email clearly says **TEST ONLY — NOT A REQUEST FOR PAYMENT**. It includes invoice number, frozen client/project names, original invoice amount, due date and the remaining test balance when prepared. The original branded test invoice PDF is attached. Internal payment notes, provider identifiers, bank/card data and secrets are excluded. HTML escapes names; plain text preserves them.

The email's only website link is the normal authenticated staff invoice page. No new bearer token is issued, no expired private link is revived and no payment capability is added. The original invoice PDF shows the invoiced amount; the reminder separately states the balance after recorded payments and credits. Real client payment-link renewal belongs to a separately reviewed live billing release.

Before delivery the worker checks the current balance, invoice version, latest stage, payment state, original delivery, owner recipient and pause/automation controls again. Paid, voided, credited or changed invoices cancel waiting reminders. The reviewed invoice snapshot supplies the PDF. PDF preparation failures are known-unsent failures; SMTP uncertainty is held. Successful sends clear message bodies but retain the reminder, original snapshot, delivery result and audit history.

Known-unsent failures may be reviewed again with a fresh current balance. A sending or sent slot cannot be sent twice. Closing an unknown send retains a persistent hold on further follow-up for the invoice; the owner can later confirm receipt after checking the inbox. A stale delivery-resolution form conflicts instead of overwriting a newer result. No reminder action modifies accounting or calls Stripe/FreshBooks.

## Validation and release

Use a disposable PostgreSQL database for concurrency and accounting checks, fictional browser fixtures and an in-memory mail backend first. Check activation boundaries, schedule/cooldown behavior, partial/full payment, credits and reversals, pause/snooze, stale signed reviews, changed recipients, PDF failures, uncertain sends and duplicate requests. Keep the live setting off on rollout; verify the authenticated page, migration, existing scheduled job and deploy health. Do not create a new overdue invoice or send another owner test email merely to populate the live screen.

Live invoicing, customer reminders, public payment links, production numbering, opening balances, tax/discount policy, provider fee/payout reconciliation and replacing FreshBooks remain separate work.
