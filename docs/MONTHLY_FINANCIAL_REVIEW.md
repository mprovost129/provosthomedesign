# Monthly financial review — sandbox only

Open `/work-projects/billing-test/monthly-review/` from invoice payments, payouts, or the billing test area. Live FreshBooks billing is excluded. No new credentials, webhook events, payment actions or scheduled jobs are introduced.

Choose a calendar month, then **Read complete Stripe month**. This performs only account and balance-history GETs through the existing restricted TEST connection and existing Balance Read permission. Full pagination is required, with a 2,000-row limit; truncation, provider errors, duplicate IDs, other currencies, FX, invalid timestamps or non-integer cents prevent replacement with partial evidence. No customer details, descriptions, bank destinations or raw provider responses are persisted.

The interval is midnight in America/New_York on the first day through midnight on the next first day, exclusive. UTC boundaries follow daylight-saving time. Provider activity uses Stripe transaction creation timestamps; app entries use their saved effective dates. These are deliberately distinct sources. Current months remain provisional and cannot be saved as completed reviews.

## Exact math and scope

All arithmetic uses integer cents; invoice Decimal amounts must convert to exact cents. Stripe's recorded fees are used rather than estimated rates.

* Payment amounts + signed refund/recovery movements − all listed Stripe costs = net before other movements and payouts.
* Net before other movements/payouts + other balance movements + signed payout/reversal amounts = change in the Stripe balance due to these rows.
* Costs = every row's attached fee − standalone `stripe_fee`, `stripe_fx_fee`, or `tax_fee` amount. This handles standalone charges with additional fee/tax and fee credits without double counting.

Payout fees are included once in costs. Payout principal is a transfer, never another sale. The computed balance **movement is not an ending balance**: opening balances are not retrieved. Gross collections can include deposits and tax; net activity is not earned revenue, profit or taxable revenue. The app does not classify other Stripe movements as income or costs automatically. Unknown types remain separate and prevent completed review.

All Stripe test account activity in the interval is included, including charges outside this app or charges not yet in any payout. Native invoice transaction links require account, source, type, amount, fee, net and existing fee reconciliation to agree. Missing or unresolved native fee history is flagged rather than omitted. Offline recorded payments and corrections are shown separately before any unknown external fees; no combined net is assumed. Work credits are non-cash reductions of debt, not another refund.

Native receivables at cutoff use issue dates and effective entry dates. Reversals only affect a prior entry when the reversal is effective by cutoff. A later void does not remove earlier principal. Positive debt, overdue debt and client credit balances are separate. Backdated corrections can alter the current reconstruction; they never rewrite saved evidence.

## Preserved evidence

Each successful Stripe read appends a checksummed source snapshot. Failure preserves old evidence and marks the month for attention. A refresh generation prevents an older overlapping read from overwriting a newer one. A prior row's disappeared or changed economics are flagged and block completion; availability-only changes do not set that provider-history warning. They can still change a saved review's comparison.

An owner can save a completed-month review only after all checks agree, the provider month was refreshed within an hour, the displayed financial fingerprint still matches, and the owner provides a note and explicit confirmation. The queue serialization lock and idempotent submission UUIDs protect saved reviews. The original report, source rows and reviewer are retained. Saved-review checksums are validated before display/export; new figures are compared to the saved fingerprint. Identical economics do not invalidate a review just because the source was refreshed. Other movements or changed-history exceptions require investigation; this release deliberately provides no bypass that marks unresolved activity as reconciled.

A saved review is not a period lock, bank receipt certification or an accounting/tax close. It does not prevent later legitimate invoice edits or certify external fees on offline payments. Refreshing source evidence and reviewing changes remains necessary.

CSV export is staff-only, private/no-store, UTF-8 with BOM and fixed two-decimal amounts. Text cells neutralize spreadsheet formula prefixes. It includes scope, timestamps, fingerprint, review issues, totals, complete provider rows, app ledger movements, and receivables at cutoff. Saved exports always use the original preserved report. No file is automatically emailed or shared.

## Verification

Focused tests cover service charges before payout, fee credits, refunds, payout fees, missing/duplicate/corrupt/non-USD data, API failure, wrong accounts, overlapping refresh generations, repeated submission references, cutoff reversals/voids, provisional months, changed source fingerprints, owner access, CSRF, CSV formula safety and full pagination. Full regression and migration checks accompany release. A separate loopback preview uses fictional data and an in-memory email backend.

Stripe reference: <https://docs.stripe.com/api/balance_transactions/list>.
