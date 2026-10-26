# Actual Stripe fees and net proceeds (sandbox only)

FreshBooks remains the real billing system. This workspace reconciles only confirmed native sandbox invoice payments, not the whole Stripe account or a bank account. It never creates a payment, refund, invoice, email or payout. Owner-controlled read-only background checks start OFF.

## Financial contract

All provider amounts are integer USD cents. Every balance transaction must satisfy `amount - fee == net`; fee detail amounts must sum to `fee`. No percentage estimates, float calculations or inferred zero fees. Fees are business costs, never a reduction in client-paid invoice principal. A $100 payment with a $3.20 fee leaves the invoice paid $100, fees $3.20, net proceeds $96.80.

A $100 refund does not automatically reverse the $3.20 fee. If Stripe retains it, gross movement becomes $0, fees remain $3.20, net becomes -$3.20. Work credits cancel invoiced work and do not count again as cash refunds. Recovery and failed-refund balance entries reverse their original movement once. Negative fee adjustments are actual provider fee credits.

Net after Stripe fees is not profit, bank deposits or accrual accounting revenue. Gross payments can include taxes and deposits; these have not been separated as earned revenue. Payment timing, expenses and other provider activity need separate reporting before a production billing cutover. No real-revenue dashboard is mixed with test figures.

## Trust and provenance

Read the matching direct Stripe account, original Checkout Session/PaymentIntent, full refunds/disputes and referenced BalanceTransactions. Validate test mode, invoice, amount, currency, source identity, exact principal agreement with the existing ledger and complete history before accepting all transactions atomically. Every provider transaction ID is globally unique; repeated polls cannot duplicate amounts. Existing economics are never overwritten: unexpected changes or disappearing records require review. Availability may progress without changing economics.

Unconfirmed fees are pending, not $0. Incomplete provider history, wrong account, FX, unsupported currency, stale accounting reads or changed provider economics suppress overall fees and net. Previous evidence stays visible but is excluded from current totals until reconciliation succeeds. A fee lookup failure never marks a paid client invoice unpaid or blocks receipts.

Financial reads reserve a generation under the existing QueueState lock, release it for network calls and verify the generation, accounting fingerprint and adjustment generation before applying. No external network call holds the accounting lock. Automatic runs are bounded to two attempts per run, with a 15-minute interval per attempt. Existing payment reconciliation runs first.

Reports use provider balance-transaction `created` timestamps in America/New_York with an inclusive date / exclusive next-midnight boundary. Refunds belong to the date they occur. Payouts are excluded so moving money to the bank cannot create another sale. Unresolved matching payments outside a period also withhold overall fees/net because their adjustment dates are unknown. A date/query filter is clearly scoped; it does not certify the entire account.

## Configuration

Existing dedicated restricted TEST key needs Balance read in addition to its current read scopes. No new write access or webhook events are needed. Credentials stay in existing private hosting settings. Activate read-only fee checks in Billing test area → Payments, fees & net → Fee reconciliation settings after authorization. Same database setting is used by the website and scheduled job. Pausing it stops future reads; audit data remains.

Uses the existing pinned Stripe SDK/API integration; no unrelated checkout API upgrade or live-mode capability is introduced with fee reporting. The existing webhook remains authoritative for principal settlement. Future SDK/API upgrades require a separate compatibility review.

Manual refresh and settings updates use normal CSRF protection and existing staff authorization; settings are owner-only. Public clients cannot access provider fee records. `reconcile_payment_fees --limit 2` is appended to the existing scheduled command and is a no-op while disabled. Pending/review counts are visible without leaking raw provider errors or secrets.

## Release verification

Verify focused math, refunds, fee credits, ACH, pending histories, duplicates, stale/concurrent reads, source/account/currency mismatches, report periods and access controls against disposable PostgreSQL. Compare app gross/fee/net rows against existing fictional card, ACH and fully refunded payments in the Stripe sandbox. Read-only verification requires no new test payments or email. Keep FreshBooks for actual billing pending further production reconciliation, tax/deposit handling, payout checks and cutover approval.
