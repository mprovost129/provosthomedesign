# Sandbox payout and bank reconciliation

This release extends the existing private billing test area. FreshBooks remains responsible for real billing. No live-mode capability, bank connection, payout creation/cancellation, payment/refund, invoice entry or email is added by payout reconciliation.

## Three independent facts

1. Stripe reports a payout as paid. This is provider status, not independent bank evidence; Stripe can later mark a previously paid payout failed.
2. The payout amount balances with its full set of provider balance transactions.
3. The owner records that a matching statement deposit was checked. In this sandbox, that bank confirmation is explicitly fictional.

A payout transfers existing money and is never counted as another sale, receipt against a client invoice or new revenue. Processing fees already included in a transaction net are not deducted twice. Standalone stripe_fee/stripe_fx_fee/tax_fee transaction amounts are shown separately, including any fees or tax attached to those transactions. The payout cost total combines these actual signed costs once; other balance adjustments are not relabeled as fees. A separate payout fee, if supplied by Stripe, is deducted once.

## Financial contract

Only standard automatic USD payouts with Stripe reconciliation_status=completed and status=paid can balance automatically. Manual, instant, reversed, non-USD and nonpositive payouts remain unsupported for this method. Pending, in-transit, failed, canceled or incomplete payouts cannot be marked reconciled or bank-confirmed.

Every provider transaction uses integer cents and must satisfy amount - fee = net; fee details must sum to the fee. Read the complete BalanceTransactions list filtered by the exact payout ID. Retrieve the payout's own balance transaction independently. Its source must be that payout, type must be payout, and amount must equal the negative payout amount.

The conservation check is:

    sum(included transaction net) + payout debit net = 0
    included net - actual payout fee = payout amount

The exact payout debit is excluded if the filtered list also returns it, so it is not counted twice. Duplicate IDs, another payout debit, unexpected currency/FX, missing history, an incorrect source or a one-cent mismatch hold the record for review. Payout fee credits retain their actual signs.

Previously verified economics and membership are retained, never silently overwritten. An economics signature also checks saved rows before displaying a balanced status. A provider snapshot stores selected fields only, excluding bank account details, descriptions and raw metadata. A transaction cannot belong to two simultaneously balanced, paid payouts. Membership can be shown historically for a failed payout and a later retry, while only the paid retry qualifies as balanced.

Invoice links use the exact BalanceTransaction ID, account, currency, source, type, amount, fee and net, and the existing invoice-cost reconciliation fingerprint. Other or not-yet-linked Stripe activity stays visibly unlinked and does not get assigned to an app invoice. Stripe payout math can balance independently; unresolved known invoice links block a new bank confirmation.

## Bank confirmations and corrections

Owner-only POST with CSRF protection, an optimistic payout version and idempotent UUID. Require a currently balanced USD payout, exact positive amount to the cent, statement date, short reference, explicit fictional-test checkbox and a Stripe check within the past hour. No bank numbers are requested or stored. A mismatched amount produces a visible difference; it cannot become a confirmed match.

Recording or undoing a bank confirmation never changes invoice paid amounts, customer debt, fees or revenue. Corrections revoke the active confirmation and retain its original amount/date/reference/actor/reason and audit history. Failed/canceled or unbalanced provider updates flag a prior confirmation for review, rather than erasing it. Concurrent confirmations cannot create two active records.

## Discovery, scope and background work

A separate owner-controlled setting starts OFF. Activation uses the same private restricted sandbox key, adding Payouts Read to the already-approved Balance Read permission. There are no new write scopes or webhook events. Existing webhook-driven payment accounting remains unchanged.

Discovery reads the complete last 90 days, up to a safety limit of 250 payouts; exceeding that limit or losing pagination fails discovery without accepting a partial list. The UI states that saved payout totals are not a complete bank statement and displays discovery coverage and failures. An exact po_ lookup handles older payouts. No bank status is inferred from an empty list. Expected arrival dates are calendar dates supplied by Stripe; transaction timestamps are shown in Eastern time.

The existing minute job now calls reconcile_test_payouts --limit 2 after payment/fee reconciliation. Discovery runs at most hourly; known payout transaction checks at most once per 15 minutes, bounded to two per run. Each payout allows up to 1,000 constituent transactions with full pagination; an oversized or incomplete history stays unconfirmed. Only test payouts from the same Stripe account as the app's confirmed sandbox invoice payments are accepted.

Provider reads occur outside database locks. The shared QueueState lock protects applying snapshots, generation/version checks, membership and bank confirmations. Late reads cannot overwrite newer results or silently replace an owner correction. GET requests never contact Stripe or create financial records. Staff read permission is required; writes require existing edit permission and bank/settings mutations require the owner. Pages are private/no-store and anonymous requests must redirect to staff login.

## Validation and cutover boundary

Meaningful tests cover exact cents, processing vs payout fees, refunds and fee credits, unlinked activity, duplicates, missing/changed history, pending/failed payouts, bank mismatches, corrections/replay, source/account/currency mismatches, saved-history integrity, access/CSRF, generation races and concurrent confirmations using disposable PostgreSQL.

Validate against existing fictional Stripe sandbox payouts without initiating transfers. A fictional bank confirmation tests application recording only and cannot establish a real bank deposit. Production payout reconciliation, independent statement evidence, tax/deposit treatment and the billing cutover still need separate validation before retiring FreshBooks.

Primary references: https://docs.stripe.com/payouts/reconciliation and https://docs.stripe.com/api/balance_transactions/list and https://docs.stripe.com/api/payouts/object.
