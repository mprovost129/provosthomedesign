# Private invoice sandbox checkout and receipts

This release connects reviewed invoice test emails to an invoice-scoped card/ACH sandbox checkout. FreshBooks remains responsible for all real billing. No live keys, real payments, new Stripe permissions, client emails or real receipt numbers are introduced. Tests are delivered only to the configured owner inbox.

## Staff workflow

Open a TEST invoice and choose **Review & email test copy**. The owner may select **Allow Stripe sandbox checkout on this private test copy**. The recipient review shows that choice and the actual owner inbox before confirmation. It is frozen into the signed review and delivery record. Ordinary staff cannot enable it. Existing deliveries default to read-only; enabling the global feature does not change them.

The recipient opens the private email link and chooses **Pay test invoice**, card or ACH, then explicitly confirms the sandbox notice. The website calculates the full current test balance; clients cannot supply the amount, currency, return URL, contact or invoice identifier. A same-origin handoff page links to Stripe without weakening the site's form-action CSP. Stripe returns to that delivery's fixed payment-status route.

The status page distinguishes open, uncertain, pending ACH, verified success, failure and a closed unused checkout. The recipient can continue or retry only their original attempt, refresh its result, or explicitly close an unused open checkout. A pending bank payment cannot be canceled this way. Returning from Stripe triggers a native CSRF-protected status recheck; it is not payment proof. Brief database-only polling updates visible status when a webhook arrives. It never creates checkouts or contacts Stripe itself.

## Access and integrity

`NATIVE_STRIPE_TEST_CLIENT_CHECKOUT_ENABLED` defaults to **False**. Both the global gate and an individually reviewed delivery opt-in are required for new checkout actions. Use the existing dedicated restricted TEST key and signed webhook; no additional credentials/scopes are needed. Configure the gate consistently on the existing Render website and scheduled job after approving the bounded owner-only trial.

All recipient routes require the existing invoice-link session, check the delivery's expiry, revocation and invoice void state on every request, and bind attempts to that delivery and invoice. Knowing a UUID or being a staff user without that link session grants no recipient access. A different invoice/session cannot read, refresh, continue, cancel, poll or download another invoice's records. Revoked or expired links do not cancel an already opened Stripe checkout; late verified payments are still accounted for in the background. An owner inbox change blocks new checkout actions until a fresh test copy is reviewed.

Anonymous checkout uses a separate authorization wrapper, not a fabricated staff identity. The existing shared queue lock, optimistic invoice version, durable nonce, one active checkout, account/amount/metadata checks, uncertain-outcome holds and verified webhook reconciliation remain authoritative. A staff-origin checkout cannot be adopted or controlled through a recipient link. IP throttling supplements a database-enforced maximum of five new attempts per delivery per hour. A cookie reset cannot bypass that limit. Status JSON returns only a private change fingerprint; no provider references, private notes, credentials or raw financial payloads are exposed.

Turning off the new gate stops new recipient checkout actions. It does not stop the existing webhook/background reconciliation, erase accounting, revoke existing invoice documents, or prevent authorized receipt downloads. Test records remain excluded from client portals, real receivables, revenue reports and FreshBooks jobs.

## Receipts

A numbered **TEST-RC** record is created once, in the same transaction as a verified sandbox payment, for recipient-origin attempts only. Open checkout, pending ACH, failed/mismatched responses and merely returning from Stripe produce no receipt. Duplicate/concurrent settlements cannot create another payment or receipt. A late success after link revocation still preserves the staff financial record, while the revoked recipient link remains unusable.

Receipts freeze the invoice number, billed client/address, project, original amount, payment method label, payment date and issuer details. Public/PDF output uses an explicit allow-list. It excludes provider/account references, staff identities, internal notes and other client projects. The branded PDF clearly says no real money was received. A receipt records the original verified payment; subsequent refunds, returns or credits do not rewrite it. The private invoice displays current aggregate balances and recorded adjustment status separately. Staff can download receipts from invoice/payment history. New reviewed copies of the same invoice can access its earlier receipts without gaining control of older delivery-origin attempts.

No automatic receipt email is sent in this phase. The owner-only invoice email link provides the receipt download after success. Customer delivery, automatic client receipts, production numbering, tax policy, provider fees/payout reconciliation and billing cutover remain separate work.

## Verification

Use fictional local fixtures first: anonymous recipient access, revoked/expired/void links, wrong invoice/session IDs, stale reviews and invoice versions, staff permissions, disabled gates, database rate limits, partial bank confirmation, duplicate/concurrent settlement, immutable receipt snapshots, safe PDF output and native CSRF return handling. The local preview uses a simulated gateway and in-memory mail only. Separately record approved actual Stripe sandbox card/ACH tests and owner-inbox delivery verification. Never describe simulation as a real provider test.

References: [Stripe Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment), [Checkout return pages](https://docs.stripe.com/payments/checkout/custom-success-page), [Receipt timing](https://docs.stripe.com/receipts).
