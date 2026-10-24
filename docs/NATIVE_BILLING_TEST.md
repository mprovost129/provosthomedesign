# Native billing test area

Staff can open **Invoices → Billing test area**, or an invoice working draft's
**Try test billing** action. This is the accounting foundation for a later
FreshBooks replacement. It is deliberately incapable of sending real invoices,
processing payments/refunds, or updating real receivables. There is no live-mode
switch. FreshBooks continues issuing real invoices and collecting payments.
Reviewed test copies can now be sent only to the owner inbox; see INVOICE_DELIVERY_TEST.md.

## Owner workflow

1. Prepare and save an invoice working draft in Documents. An accepted estimate,
   proposal or change order can prefill it. New drafts preserve that exact source
   revision; older drafts remain unlinked rather than guessing their source.
2. Choose Try test billing. Review the selected saved version, optional fixed
   discount and optional whole-plan tax percentage. Create a test billing plan.
   The service breakdown, customer/address, dates and terms are frozen.
3. Create a deposit, progress invoice or final invoice. Choose a dollar amount,
   percentage of the full plan, or the remaining unbilled amount. Confirm the
   explicit test-only checkbox. The final invoice must use the full remainder.
4. Review the numbered TEST-INV record and download a watermarked PDF. Payments
   made elsewhere can be simulated using check, cash, ACH/bank, card, Zelle,
   PayPal, Venmo or Other. These are record labels, not payment integrations.
5. Apply a numbered TEST-CR credit or reverse a mistaken entry with a reason.
   Reversals preserve both records. To correct invoice amount/dates, first
   reverse active payments/credits, then void and create a replacement.
6. To correct a plan's frozen pricing, void all its invoices and choose Close
   test plan with a reason. Create a replacement from the working draft. Old
   plans, numbers and invoices remain available; closed plans cannot bill again.

## Accounting rules

- USD only; fixed two-decimal amounts and authoritative Decimal arithmetic.
- Tax defaults to zero. The user supplies the percentage; this does not determine
  tax applicability, fetch tax rates, support line exemptions or file tax returns.
  Tax is rounded half-up after subtracting the plan's fixed discount.
- A deposit is an installment of the total, not an extra charge. All active
  invoice amounts combined may not exceed the plan. A second deposit after any
  active invoice is rejected. Progress invoices may also be the first invoice.
- Each installment allocates the plan's subtotal, discount and tax. The final
  installment absorbs rounding cents. Voids release the original allocations.
- Credits and payments reduce the amount due on their invoice. They do not
  release work to be billed again. Overpayments and excess credits are rejected;
  unapplied customer cash and credit balances are not supported in this release.
- Every saved working draft can have one active frozen test plan. For linked sources,
  a source estimate/proposal/change-order record may have only one active plan across
  its accepted versions. Only the selected document's services are included;
  approved change orders require their own working drafts/plans. Hourly source
  estimates must first be converted to an explicit fixed billing scope.
- Unlinked drafts cannot detect overlapping work or real FreshBooks invoices.
  Review those manually. Existing plans are frozen if their draft is edited.
- TEST-INV and TEST-CR sequences are independent of FreshBooks and EST numbers.
  Voided numbers are not reused. An atomic rolled-back attempt consumes no number.
- Payment/credit dates must be between invoice date and today. Invoice issue
  dates cannot be in the future; due date cannot precede issue date.
- States derive from immutable invoice amount and active ledger entries:
  open, partially paid, overdue, paid, settled with credits, fully credited, void.
  Overdue takes precedence over partial when an unpaid balance is past due.

## Integrity and access

All mutations require active staff with view/change-workitem permissions, CSRF,
and the existing staff security checks. View-only staff can inspect records.
Mutation services serialize on QueueState before checking current balances,
versions, source links and nonce references. Number allocation and ledger writes
are in the same transaction. Repeated submissions with identical references are
idempotent; reusing a reference for different details is rejected. Stale actions
return a reload/review error. No destructive edit/delete controls are exposed.

Invoices freeze their billing snapshot. Payments, credits, reversals and voids
produce dated actor-linked audit events. Payment notes remain staff-only and
are never included in the invoice PDF. All routes require staff, disable
caching, and are absent from public tracking/client portal routes. Real invoice
reports, owner revenue summaries, reminders and FreshBooks jobs do not query
these tables. Migration 0051 is additive; existing records are not reclassified.

## Required before actual billing migration

Owner approval of live numbering and opening receivables, a real invoice issue/
delivery workflow with recipient review and immutable delivery snapshots,
gateway integration and webhook reconciliation, credit/refund accounting,
tax/discount policy and payment-method fees, reconciliation against FreshBooks,
and cutover/rollback checks. Test records must remain excluded after cutover;
do not remove the TEST prefix or repurpose these trial invoices as real invoices.
