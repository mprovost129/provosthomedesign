# Stripe sandbox invoice payments

This is a staff-only trial integration, not a live billing cutover. FreshBooks still handles all real invoices and payments. The test ledger and TEST-INV numbers remain separate from real receivables, client balances, revenue reports and client portals. Existing owner-only invoice email links do not gain payment or account-statement access.

## Staff workflow

Open **Billing test area > Online payment testing** to review the connection and attempts. On an unpaid test invoice, choose **Try Stripe test payment**, select card or ACH, confirm the full remaining test balance, then choose **Continue to Stripe test checkout** on the handoff page. This normal link preserves the site's strict same-origin form protection. Only Stripe test payment details and fictional payer details should be entered. This app never collects full card or bank credentials.

The server calculates the amount. A deposit should use its own installment invoice. Checkout accepts balances from $0.50 through $999,999.99 USD. No tips, coupons, taxes, optional items or browser-supplied amounts can alter this checkout total; tax/discount allocations remain part of the previously frozen invoice.

Returning from Stripe is not proof of payment. Verified results from Stripe update status. ACH stays pending until the PaymentIntent succeeds and Checkout is paid. **Refresh Stripe status** performs an immediate recheck; the existing scheduled job also checks known sessions. Closing the window or returning through Cancel does not cancel a payment. **Close unused checkout** asks Stripe to expire it and releases the hold only after a verified closed result. Processing ACH payments cannot be canceled by this action.

## Connection

Create a dedicated restricted key in the intended Stripe **test environment**, with account identity read, Checkout Sessions write/read, PaymentIntents read and Charges read permissions. Never reuse a live key. If Stripe requires additional permissions for a Checkout dependency, review and approve that precise addition. The application never requests refunds, transfers, payouts, subscription changes or saved payment-method reuse.

Configure both the existing Render web service and scheduled job privately:

* `NATIVE_STRIPE_TEST_SECRET_KEY`: the dedicated `rk_test_` / `sk_test_` key.
* `NATIVE_STRIPE_TEST_WEBHOOK_SECRET`: the endpoint's `whsec_` signing secret.
* `NATIVE_STRIPE_TEST_ENABLED=True`: enable last, after both secrets are saved.

The application refuses live-key prefixes and never falls back to `STRIPE_SECRET_KEY` or other commerce settings. Development settings default to disabled/blank and never import production credentials. No publishable key or Stripe JavaScript is needed for hosted Checkout. Credentials and complete hosted Checkout URLs must not be printed in logs or committed to source.

Register the direct-account snapshot webhook at:

`https://www.provosthomedesign.com/billing-test/stripe/webhook/`

Subscribe to `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `charge.refunded`, `charge.dispute.created`, and `charge.dispute.closed`. Use the API version matching the pinned Stripe SDK where supported. The handler verifies Stripe's signature over the raw body, rejects live/Connect events, deduplicates event IDs, and retrieves the current Checkout state with the configured test key before settlement. Unknown or unrelated events do not create payment records. Payloads are capped at 256 KiB; raw financial event bodies are not persisted.

## Integrity and recovery

Invoice writes and settlements use the same QueueState lock. An open/pending/uncertain attempt blocks other payments, credits, reversals, voiding and new attempts. The database also permits only one creating/open/processing checkout per invoice. No network request is made while holding that database lock.

The outbound idempotency key is the durable random attempt ID, and all create parameters (including expiry) stay fixed across retries. An uncertain creation keeps its reservation. Never automatically release it because of a network timeout or local clock expiry. If the session reference is unknown, retry the same attempt; after 23 hours, retrying creation is prohibited because Stripe may prune its idempotency record. Recover the exact `cs_test_` reference from Stripe's request logs; the app verifies the attempt metadata, account, method, currency, amount and session before binding it.

Each attempt freezes the Stripe account ID and rejects a different account after credential rotation. Settlement requires test-mode Checkout and PaymentIntent objects, USD, the exact invoice attempt metadata/reference, exact amount received, and a succeeded intent. A single protected ledger entry records settlement, so duplicated or concurrent events cannot apply it twice. A stale unpaid response cannot undo a recorded payment or revive a closed attempt. A late payment that conflicts with other reservations or a changed balance is held for review, never over-applied.

Known sessions are rechecked by `reconcile_test_payments` (default limit 10) as part of `run_queue_jobs`, only when the sandbox is explicitly enabled. It never opens new checkouts or submits payment details. Paid attempts are also revisited so missed adjustment events can be detected. Provider errors preserve reservations and use safe messages without credentials, financial details or raw provider responses.

Refunds/disputes are **flagged for review, not automatically posted to balances** in this release. The original gross payment record stays intact; the payment page warns that its balance is not reconciled after an adjustment. Manual ledger reversal of a gateway-confirmed payment is blocked because a bookkeeping reversal is not a refund. Do not treat this sandbox as complete refund or dispute accounting. Automatic refund/return accounting, fees/payout reconciliation, client payment receipts and live payment access remain required before real billing cutover.

## Validation

Automated checks use fictional local records and simulated Stripe responses, including raw signed webhook payloads, duplicate/concurrent settlement, pending/failed ACH, protected invoice edits, account/mode/amount mismatches, unsafe URLs, old uncertain attempts, recovery, provider downtime and refund/dispute review. Browser preview uses a loopback-only fictional database and simulated gateway. Separately test the deployed connection with Stripe's official card and bank test data; distinguish real sandbox API results from local simulation in the release record. Never use live credentials, real payer bank details or real financial transactions for these checks.

Official references: [Hosted Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment), [ACH Checkout](https://docs.stripe.com/payments/ach-debit/accept-a-payment?payment-ui=checkout), [Webhooks](https://docs.stripe.com/webhooks), [Idempotent requests](https://docs.stripe.com/api/idempotent_requests).
