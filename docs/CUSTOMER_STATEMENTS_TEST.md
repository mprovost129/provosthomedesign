# Customer statements (billing test area)

Staff with queue view permission can open **Billing test area > Customer statements**, or **Test billing statement** on a CRM client. A test invoice also links to its client's statement. Search is by client name. Statements are read-only; use the linked test invoice to correct an entry.

This release uses only native test invoices, not FreshBooks or real opening balances. It does not email statements, expose them through invoice bearer links, create charges or enable live billing. Every PDF page says TEST ONLY / NOT FOR PAYMENT.

Each request materializes one current projection under the same queue lock used by ledger writes and CRM merges. It includes all explicit merged-client ancestors; unrelated companies stay separate even if names, owners or contact addresses match. Current CRM billing name/address appear at the top; original billed names remain on open invoice rows.

All activity starts at zero: invoices increase the balance; payments and credits decrease it; entry reversals restore it; voids subtract the original invoice. Totals reconcile to the sum of current invoice balances. Net payments and credits exclude reversed entries. Aging uses remaining balances and today's local date: due today is not overdue; then 1-30, 31-60, 61-90 and over 90 days.

This is a current statement, not a historical as-of report. Effective invoice/payment/credit dates order the activity; reversals and voids use their recorded local dates. Backdated entries are included, so an intermediate running balance can be negative when corrections happen later. No arbitrary historic date selector is offered. Same-day ties use recorded timestamps and stable invoice ordering.

HTML activity is paginated 50 rows; the PDF includes all activity with repeated column headers and page numbers. PDFs and HTML require staff access, are private/no-store/noindex, and exclude CRM/internal accounting notes, payment references, contact email lists and authentication links. The PDF is generated on demand, not an immutable issued statement. Download again after edits to get an updated statement.

Before live billing: configure approved real invoice numbering, opening balances, payment providers, tax policies and reconciliation. Statement delivery and client portal financial access need their own authorization and scope; an invoice link must never grant access to the entire client's account.
