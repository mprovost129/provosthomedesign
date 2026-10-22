# Staff pricing calculator

The private staff workspace includes Pricing, with links from each client and project. The calculator stores itemized estimates and can create a reviewed proposal draft. It never sends a proposal or creates an invoice automatically.

## Client categories

Staff choose Homeowner, Contractor or Other when adding a client manually. The same dropdown is available when editing an existing client. This pricing category is separate from Individual/Company, booking access, and the client's project history.

Existing clients and clients created automatically from public intake stay unclassified until staff choose their category. No classification is inferred from a name or company. Old browser edit forms that lack the new field preserve the stored category.

Selecting a client in a new calculation selects their category and its latest configured rate card. Homeowner and Other do not fall back to Contractor rates. They remain unconfigured until an owner saves their complete rates. Existing data is preserved.

## Contractor guide

Initial rates are sourced from Provost Home Design Contractor Pricing.pdf:

| Service | Rate | Area basis |
| --- | --- | --- |
| New construction | $1.25/sq ft | Living area, including finished basements |
| Attached garage / large unfinished space | $0.75/sq ft | Excludes unfinished basements; do not repeat living-space area |
| Detached garage | $1.25/sq ft | Footprint plus any upper-floor area |
| Addition design | $1.25/sq ft | New living area |
| Draw existing structure | $0.75/sq ft | Entire structure area being drawn, including garage |
| Renovation design | $0.75/sq ft | Only the area being renovated |
| Hourly alternative | $125/hour | Meetings, site visits, drawing, phone calls and other project time |

Existing-condition drawings and renovation design are separate services with separate area fields. For example, 2,000 sq ft of existing drawings plus 600 sq ft of renovation design totals $1,950. Hourly mode is an alternative to the area services, rather than an additional charge on top of them. No minimum fee, tax or deposit is inferred.

## Workflow

1. Classify the client, then open New calculation from Pricing, their client record or their project.
2. Select the client/project when known, and review the category and rate-card version. Unassigned quick calculations can also be saved.
3. Enter only the areas used, or select Hourly estimate and enter hours. Leave unused areas blank. Multi-unit or other unusual projects require individual review.
4. Preview or save. Service amounts use Decimal arithmetic and half-up rounding to cents per line. The browser working total is a convenience; the server recalculates and validates every save.
5. A one-time rate change requires private justification. An optional additional fixed-price service requires a description. An optional quoted fixed-fee override requires a private reason and becomes a single agreed-fee line in the proposal.
6. Before creating a proposal, assign a client and one of their projects, write the deliverables, review the scope and confirm the review checkbox. Individual-review projects also require a private review note.
7. Open the proposal draft and use the existing proposal review/send workflow. Private rate explanations, estimate notes and review notes are not copied into client-facing proposal content.

## History and permissions

Each save adds a calculation revision with a snapshot of quantities, prices, category, client/project names and source rate version. New rate cards create versions rather than altering old prices. Editing a client category never silently reprices a saved estimate or proposal. Conversion of an estimate with an outdated client category is blocked until staff review and save the matching category/rates.

Each calculation revision can create one proposal draft. Retries reuse the existing draft. Later calculator revisions do not update a draft or sent proposal already created. Draft edits continue through the existing proposal editor.

View-work permission is required to read pricing; change-work permission is required to calculate, save and create drafts. Only an active staff superuser with work permissions can create category rate versions. All mutations retain CSRF protection, optimistic version checks and the queue transaction lock. Staff pricing pages are not public and use the existing staff verification/security controls.

Migrations 0046 and 0047 add pricing records and the optional client category, and seed only the contractor card. No clients are automatically classified, and no existing proposal, invoice, queue position or contact information is changed.
