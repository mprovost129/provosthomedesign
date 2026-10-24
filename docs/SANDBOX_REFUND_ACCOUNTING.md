# Sandbox refunds and payment adjustments

This release continues the staff-only billing trial. FreshBooks handles all real billing. No live keys or client payment links are enabled. No customer email is sent by the refund flow.

## Owner workflow

Open **Billing test area > Online payment testing**, select a confirmed payment and choose **Prepare test refund**. Enter the partial/full amount, an internal reason and one of two treatments:

* **Refund only:** returned funds reopen the amount due. The service charge remains owed.
* **Refund and credit canceled work:** a numbered TEST credit is posted only when the refund succeeds. It cancels that amount of work. A later refund failure restores the payment and reverses its linked credit through new history entries.

Review the exact amount/treatment, then explicitly confirm. Only an active staff superuser can issue refunds. Read and ordinary refresh actions follow existing staff permissions. Confirmation uses an actor-bound signed review valid for 15 minutes, a durable request nonce and invoice version. GET requests, webhooks, periodic jobs and status refreshes never issue refunds.

Pending/uncertain refunds hold conflicting invoice edits, credits and checkouts. They do not reduce recorded payments until Stripe confirms success. Retry the same request after an uncertain creation; never create another one. After 23 hours, creation retries are prohibited because Stripe can prune idempotency records. Refresh the full refund history to recover the original metadata-bound request instead. No force-release or guessed refund reference is supported.

## Connection

Refund writes default to off: `NATIVE_STRIPE_TEST_REFUNDS_ENABLED=False`. Set it to True only after explicit approval of the dedicated restricted test key's refund-write permission and tests. The existing dedicated test key is reused; live-key prefixes remain prohibited. Retain Checkout Sessions write, PaymentIntents read and Accounts read. Stripe's combined **Charges and Refunds** permission must allow writes to issue refunds; add **Disputes read** for dispute reconciliation. This code contains no charge-creation, dispute-response, payout or live-refund action. Keep all credentials private in the existing Render web and cron services.

Extend the existing direct-account signed snapshot webhook with `refund.created`, `refund.updated`, `refund.failed`, `charge.dispute.updated`, `charge.dispute.funds_withdrawn`, and `charge.dispute.funds_reinstated`. Keep the previously subscribed checkout lifecycle, charge.refunded, and dispute.created/closed events. Existing five-minute polling is a fallback for missed events. No raw refund/dispute bodies, evidence, card details or bank credentials are persisted.

## Accounting and recovery

The original payment is immutable. Separate refund, return and recovery ledger entries change net payments. Provider adjustments are connected to the exact test account, intent, charge, currency and amount. Full refund/dispute history is paginated (bounded to 500 objects per type); missing/invalid history holds the payment for review and preserves existing entries. Webhook bodies are notifications only: current provider data is retrieved before applying any accounting. Request and provider identifiers are unique, and invoice writes share the queue lock. A per-payment read generation prevents an older concurrent response undoing a newer reconciliation. No provider network calls occur under that database lock.

For disputes and ACH returns, only balance-transaction principal withdrawn/reinstated is posted; fees never become client debt. Open disputes hold invoice changes. Inquiries without withdrawn funds do not create a return. Won disputes restore confirmed principal. Lost disputes reopen that part of the invoice without automatically canceling work. Evidence and dispute responses remain in Stripe. Overlapping refund/chargeback totals beyond the original payment require review instead of silently double-debiting a client.

Refunds made directly in the Stripe sandbox are imported as refund-only; the app never assumes canceled work without an explicit reviewed request. Failed refunds append recoveries; if the client already repaid or the invoice was voided, a negative balance is an overpayment/account credit and must be reviewed. No automatic new refund or charge is generated. Statements retain and reconcile all invoice, payment, refund, credit, reversal and recovery activity. Dates are when adjustments were recorded; this is a current statement, not a backdated provider statement.

This is not a complete general ledger. Fees, payout/bank reconciliation, live refunds, real numbering, tax credit-note compliance, customer refund receipts and dispute evidence workflows remain outside this trial. Do not switch real billing until those are reviewed.

## Verification

Local tests use isolated fictional records and simulated provider responses, including partial/full/pending/failed refunds, linked credit corrections, recovery after repayment/voiding, stale reads, duplicate events, concurrent reconciliation, signed confirmation, staff permissions, incomplete history and disputes with principal/fee separation. Local preview uses a simulated gateway and never reads production credentials. Approved actual Stripe sandbox tests must be recorded separately from simulation; never use real card/bank details or real financial transactions.

Official references: [Refunds](https://docs.stripe.com/refunds), [Refund API](https://docs.stripe.com/api/refunds/create), [Dispute object and balance transactions](https://docs.stripe.com/api/disputes/object), [Idempotent requests](https://docs.stripe.com/api/idempotent_requests).
