# FreshBooks Payments reconciliation

FreshBooks is the live billing authority. This owner-only workspace compares an original Online Payments transaction CSV with complete, recent invoice and payment mirror scans. It makes no provider calls or billing changes and never reads native sandbox billing or historical CSV invoice totals.

## Source and workflow

Open Invoices → Payment reconciliation. In FreshBooks Payments → Online Payments, select the full payment month and All statuses, clear other filters, and Export CSV. Upload the original file, confirm the selected business and filter coverage, preview, and compare. A CSV has no account ID or filter metadata: owner attestation supplies that context, while row-level invoice/payment matches supply corroboration. A matched subset does not establish independently complete account coverage.

Observed export columns, October 30, 2026: Source, Source Number, Source Type, Transaction Date, Transaction ID, Gateway, Fees Paid, Amount, Currency, Method, Status. Unsupported headers, duplicate transaction IDs, malformed rows, missing offsets, excessive sizes and meaningful sub-cent values are rejected. Three-decimal values such as 20.600 are exact cents and accepted. Dates are converted to America/New_York; UTC/Eastern boundary transactions require review. Out-of-period rows are counted and excluded.

Official source: https://support.freshbooks.com/hc/en-us/articles/29437545494541-How-do-I-review-my-online-payments . The basic payment API does not document actual fee/net fields: https://www.freshbooks.com/api/payments . No new OAuth scope is needed.

## Mathematics and scope

Use integer cents throughout. Group by source currency. Successful online gross minus actual source processing fees equals net collections after processing fees. Subtract each fee once. Missing fees are unknown, never zero. Declined/failed attempts are excluded. Pending/refunded/disputed/unknown statuses require separate evidence and prevent a complete net result for their currency. The initial export does not provide reliable refund amounts or refund fee treatment; never infer either from status. This is a verification workspace, not profit reporting, tax revenue recognition, a general ledger or a month-end receivables statement.

Match an invoice by its source number and a payment by linked provider invoice ID, Eastern date, exact gross and currency. Multiple identical candidates, reuse of the same payment, changed billing identity/currency, invoice balance inconsistency, non-invoice sources and another gateway require review. Unmatched saved payments remain visible, with known online methods flagged. They are not silently added to online totals or assumed fee-free.

Mirror captures take the shared queue lock used by synchronization. Both resources must have successful scans less than 24 hours old, correct business identity, complete ID coverage, matching generations, matching source fingerprints and source/row IDs. Failed or in-progress scans cannot produce a new comparison. Existing comparisons remain available. Deleted and credit-applied payments are excluded; archived source facts remain eligible. Invoice total/paid/outstanding values are current saved facts, not balances as of the selected historical month.

## Persistence and safety

Exports retain account, business, month, file fingerprint, normalized allowlisted source rows, importer and time. Same file/account/month reuses evidence. Comparisons retain input versions/hashes and allowlisted facts; no browser cookies, tokens, payment notes, card numbers or bank details are stored. Previous results are never rewritten on recheck. Application routes expose no edit/delete operation for evidence. Fingerprints detect accidental corruption; they are not protection against a privileged database attacker.

Routes require existing staff workspace access plus an active superuser. Imports/comparisons also require change-work-item permission and reject preview mode. Standard CSRF protection and private/no-store/noindex responses apply. All source text is template-escaped. No financial CSV exports or raw HTML rendering are introduced.

Payouts and bank confirmation are deliberately not asserted by this report. Payout timing, opening/closing processor balance, refunds, disputes and adjustments can differ from payment-month totals. A matching payout subtotal is supporting evidence, not proof of a bank deposit. FreshBooks remains the production billing and payment system.
