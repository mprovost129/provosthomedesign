# Change orders

## Staff workflow

1. Open Documents, then the current accepted estimate or proposal.
2. Choose **Add change order**. The original agreement's client and project are
   retained; this is a separate document tied to that exact accepted version.
3. Describe the additional scope and deliverables. Add service quantities/rates,
   notes and terms using the existing document editor and rich text controls.
4. Choose whether the schedule changes. If it does, provide an explanation and,
   optionally, estimated additional calendar days and/or a proposed completion
   date. These do not automatically edit queue dates or book an appointment.
5. Save and preview the additional price, original price, previously approved
   changes, and revised agreement total. Download the branded PDF if needed.
6. Review the recipient and explicitly confirm before emailing the private link.
   Client acceptance or a request for changes uses the existing private response
   flow. Both client and owner receive the recorded response.

Drafts do not send emails. They appear in Documents, the project workspace and
the original agreement's related change-order list. Sent copies appear in the
client portal only with the existing financial-access/client/project grants.
Private invitation access remains scoped to the single sent version.

## Pricing and history

The price of a change order is the additional service total, not the revised
whole-project bill. Original services are not copied or charged again. The
revised agreement total is original accepted services plus accepted change-order
services plus this proposed addition. Hourly pricing is explicitly estimated.
Separate agreements for the same project are not silently combined.

Names use the parent number plus CO-01, CO-02, etc. Number allocation and saves,
sends and responses use the existing queue transaction lock. A single agreement
can have multiple private drafts but only one pending sent change order at a
time. Finish or withdraw that version before sending the next one. Previously
approved changes are rechecked at response time to reject stale totals.

Accepted versions, original prices and acceptance records remain unchanged.
Accepted change orders cannot be revised away; further additions require another
change order. A base agreement with pending or accepted changes cannot be
rewritten through the old revision action. Unaccepted change orders can be
withdrawn or revised through the existing flow. Revisions keep schedule details.

The first release supports added services and zero-price clarifications, not
negative-price credits. Credits and refunds remain part of the billing workflow.
FreshBooks remains the invoice/payment authority. Approval does not create an
invoice or change an existing FreshBooks invoice. Invoice preparation copies
services from the selected document only; staff must review the base agreement
and approved change orders in FreshBooks to avoid missing or duplicating charges.

## Deployment and checks

Migration 0050 adds a nullable protected parent-version link to Proposal. Existing
agreements are unchanged. No new credentials, OAuth permissions, hosting services
or background schedules are required.

Regression coverage includes preserved originals, pricing math, response
deduplication, draft/send idempotency, version conflicts, schedule validation,
concurrent sends, stale pricing, withdrawn versions, portal isolation, private
invitation boundaries, CSRF, PDF content and unchanged FreshBooks/queue state.
Local browser and PDF samples use fictional clients only; no test messages are
sent to real clients and no production agreement is accepted for testing.
