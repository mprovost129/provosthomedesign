# Provost Home Design document studio

## Current release

Staff navigation → **Documents** opens the shared workspace. Choose Estimate,
Proposal, or Invoice draft, choose the CRM client, load that client's projects,
then open the editor. Project and client records also link to their documents.
Creating a document requires an existing project/client relationship.

The original PHD design uses the existing roof logo, navy/slate colors, a serif
document title, structured service rows, and a live paper preview. It takes
familiar business-document conventions from FreshBooks without reproducing its
interface, artwork, or templates.

The editor provides:

- Saved CRM billing/project addresses, document date, reference, fixed/hourly
  pricing, and up to 20 ordered service lines.
- Quantities, rates, and authoritative server-calculated USD totals. Each line
  rounds half up to cents before summation. Existing-condition drawings and
  renovation design remain separate billable services.
- Bold, italic, underline, bullet and numbered lists for scope, deliverables,
  revisions, exclusions, timing, client notes and terms. Formatting is allowlisted
  and checked against the corresponding plain text before storage/rendering.
- Save → preview → PDF; estimates/proposals retain explicit recipient review
  before sending through the existing private acceptance flow.
- A short estimate may omit scope/deliverables; a proposal requires both.
  The existing EST numbering sequence is retained for both document labels to
  avoid renumbering historical records. Draft type can be changed in the editor.
- Existing sent/accepted snapshots and historical PDF rendering remain unchanged.
  New drafts use the PHD studio format. Revising a sent document follows the
  existing version/acceptance rules; it never silently edits an accepted version.
- Accepted estimates/proposals can prefill an invoice working draft. This copies
  services and terms without altering the accepted document.

## Billing boundary

FreshBooks remains the authority for issued invoices, balances and payments.
**InvoiceDocument is a working paper, not an invoice ledger.** It has a DRAFT
reference, not a legal invoice number; saved revisions preserve the then-current
business/client details. The page and every PDF page clearly mark it unissued.
No public sharing/send route, receivable, FreshBooks mutation, payment, customer
invitation, or invoice email is created by saving it. The original FreshBooks
invoice workflow remains available separately.

The editor totals services only. Taxes, discounts, deposits and payment schedules
must be reviewed in FreshBooks before billing. There is no automatic transfer of
an edited native invoice working draft to FreshBooks in this release.

Migration 0049 adds two tables only: working documents and their immutable saved
revisions. It does not rewrite existing proposals, contacts, invoices or payments.
Staff view/change permissions, CSRF protection, no-cache responses, queue locks,
idempotency and optimistic version checks apply. No new credentials or scopes
are needed.

## Recommended next releases

1. Review the editor with actual business examples, then add reusable services,
   approved terms/templates and clear proposal change orders. A change order
   should reference an accepted version, show the price/time difference, and
   require separate acceptance. Do not overwrite the original agreement.
2. Build the native invoice ledger before collecting payments: unique invoice
   numbering, immutable issued versions, due dates, discounts/taxes, deposits,
   progress invoices, credits/voids, payment allocation, audit history and reports.
   Decide the historical FreshBooks migration and reconciliation plan first.
3. Add payment processing for cards and ACH, plus recorded checks and cash, using
   an established provider's hosted payment page, signed events and idempotent
   reconciliation. Keep credentials/card data out of application forms. User also
   wants Zelle, PayPal and Venmo considered later; availability and reconciliation
   must be verified before promising a provider integration.
4. Run a defined parallel comparison against FreshBooks, reconcile balances and
   reports, then explicitly approve the billing cutover. Avoid issuing the same
   invoice from both systems.

## References reviewed

- [FreshBooks invoice workflow](https://support.freshbooks.com/hc/en-us/articles/216631328-How-do-I-create-an-invoice)
- [FreshBooks estimate workflow](https://support.freshbooks.com/hc/en-us/articles/115006136447-How-do-I-create-an-estimate)
- [FreshBooks proposal workflow](https://support.freshbooks.com/hc/en-us/articles/115013162648-How-do-I-create-a-proposal)
- [FreshBooks brand appearance](https://support.freshbooks.com/hc/en-us/articles/115001971307-How-do-I-customize-my-brand-appearance)

These references inform workflow categories only. PHD layouts and implementation
are independently authored. No FreshBooks customer data or third-party visual
assets are included in the samples.
