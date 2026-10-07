# Unified client intake queue tracking and appointment booking

Scope updated October 7, 2026 for Provost Home Design. Build in the existing Django website at `C:\Users\mprov\Desktop\Django\provost_home_design`. The web app database will become the authoritative work queue after a checked migration of the existing Google Sheet. Google Calendar remains the appointment calendar.

Clients use one public form link and choose New Submission or Update. Both paths accept documents and pictures. Mike manages all work from one staff-only queue page. Every submission gets an on-screen confirmation and client and owner emails. Clients can return with their project/request ID to see current position and status. An update has its own queue position and remains connected to its project's history.

## One public form link

Use one canonical address, provisionally `/submit-work/`. The first required question is **Is this a new submission or an update?** with choices **New Submission** and **Update**. Display and validate the selected path. Preserve common answers when switching paths; ignore hidden, inapplicable fields on the server. Existing website links and plan-catalog entry points lead into this same form with relevant plan references.

Returning clients also have Track My Project and Book an Appointment actions. Neither requires a new work submission. Internal notes, priority, status and scheduling controls stay in the private management page.

## Required contact and address questions

| Exact question | New submission | Update | Stored field |
| --- | --- | --- | --- |
| Full name | Required | Required | contact_full_name |
| Company | Required | Required | company |
| Email address | Required | Required | contact_email |
| Phone number | Required | Required | contact_phone |
| Billing street address | Required | Not shown | billing_street |
| Billing city | Required | Not shown | billing_city |
| Billing state | Required | Not shown | billing_state |
| Billing ZIP code | Required | Not shown | billing_zip |
| Project street address or lot | Required | Identification input, not a repeated full address form | project_street |
| Project city | Required | Use connected project context | project_city |
| Project state | Required | Use connected project context | project_state |
| Project ZIP code | Required | Use connected project context | project_zip |

Company help text on both paths: **If you are not submitting for a company, enter Homeowner.** In the queue, display the person's full name alongside Company; do not label every homeowner simply Homeowner.

Offer **Project address is the same as billing address**, optional, to copy all four fields while leaving them editable. Store billing and project addresses separately. Preserve ZIP codes as text, including leading zeros. Keep full names intact without guessing how to split them. Allow common phone formatting and extensions. Anonymous submissions retain contact/address snapshots and do not overwrite an existing verified client's profile or billing details.

## Relevant project questions and uploads

Combine the initial request with uploads so a client does not need another form solely to send documents. Reuse useful Google-form wording and choices, with relevant conditional sections for service-specific information.

| Question | New submission | Update |
| --- | --- | --- |
| Project name, if you use one | Optional; address supplies a display name when blank | Identification input; see linking rules |
| Project ID, if you have it | Not needed | Optional; accepts an existing project or request reference |
| What do you need help with? | Required | Use connected project context; collect only if needed |
| Briefly describe what you would like to do. | Required | Not shown |
| Please describe the files, update, or requested change. | Not shown | Required |
| What are you sending? | Optional; require a selection when uploading | Required |
| Upload plans, surveys, sketches, or photos | Optional | Optional |
| File or folder link, if you prefer | Optional, HTTPS | Optional, HTTPS |
| Requested response or drawing date, if applicable | Optional | Optional |
| Approximate size or scope, if known | Optional | Not shown |
| What is your preferred timeframe for design work? | Optional | Not shown |
| Plan number, if you are asking about a particular plan | Optional | Optional when relevant |
| How would you prefer us to contact you? | Optional | Optional |
| How did you hear about us? | Optional | Not shown |
| Anything else we should know? | Optional | Optional |

Service choices: New custom home; Addition or renovation; ADU or garage; Stock-plan modification; Framing plans; Other / not sure. Attachment categories use checkboxes with **Select all that apply**: Plans or survey; Photos or sketches; Answers or corrections; Revision request; Other.

Preserve relevant details from the detailed Google questionnaire: home preferences, plan changes, framing scope, site constraints, requested dates and project contacts. Use conditional or optional sections so unrelated questions do not become mandatory. Preserve original question codes and complete answer snapshots for migration and history.

Both paths accept text-only requests without files. Uploads stay private. Validate allowed types and actual content, generate safe storage names, retain original names as metadata, show progress and failures, and preserve entered answers on failure. Owner email links lead to authorized file access.

The user selected ten files up to 100 MB each, retaining the working Google upload allowance. Stage 2 implements that allowance through private persistent storage/direct uploads; the older website form's five-file/10-MB allowance is not the unified intake limit.

## Connected updates with independent positions

Every distinct New Submission and Update creates its own **New** work item, permanent request reference and position at the end of the active queue. Reprocessing the same submission does not create another item. An update links to a shared Project record and optionally the previous work request, retaining history without merging queue entries, changing the previous request's status or resetting its order. Updates to completed projects create fresh active work without reopening the completed item.

Project linking works as follows:

1. **Project ID supplied:** accept an existing project/request reference as a linking candidate. Verify that the client is authorized for that project before revealing details or attaching automatically.
2. **ID unavailable:** allow project name or project address. After email verification, offer a searchable picker containing only that client's authorized projects, displaying their names and addresses. The client can select a project without locating its ID.
3. **Matching:** within authorized projects, a unique exact normalized name or full project address can identify a candidate. Preserve unit/lot numbers and locality information. Partial or fuzzy matches are suggestions, never silent automatic links. Address/name alone does not prove ownership.
4. **Ambiguous or unmatched:** save and queue the work immediately with its submitted name/address and flag **Project link needs confirmation** on the same queue row. Mike can choose the correct project there. Linking can occur after client verification or owner confirmation without changing the new item's ID or position.

Require enough project context to distinguish the work: a selected project, known ID, project name or project address. The ID itself is optional. Keep the submitted project context as history even after a link is corrected. Never link solely because two submissions share an email, company or person's name; clients may have multiple projects and properties may have multiple authorized participants.

Suggested initial identifier design: retain **PHD-00023** style references for individual work requests. A Project record has an internal UUID and a canonical display reference based on its first work item. Every update receives a new work reference, such as **PHD-00026**, and displays its connected project. Tracking accepts either known reference, identifies the shared project and shows the relevant request plus its related work items. Avoid asking clients to remember a second unrelated numbering system. If an item is linked later, its receipt reference remains valid.

## One private queue management page

Use a dedicated staff-only page, provisionally `/work-queue/`, with inline edits and an expanding detail drawer on the same page. Routine work does not require navigating to separate admin edit screens.

- Search by ID, client, company, email, project name or address; filter Active Work, All Work, completed work, status, priority and received dates.
- View contact information, separate billing/project addresses, descriptions, full answers, private files and related project work.
- Edit status, priority, queue order, estimates, requested/committed dates, scheduled start, follow-up date and internal notes.
- Add work manually for a call or email; no separate internal form required for daily use.
- Link or correct a work item's project from the row. View linked items together while keeping their individual positions. Keep linking changes audited and reversible.
- Mark Completed, Declined, Canceled, Duplicate or On Hold. Closed work leaves Active Work automatically and remains in All Work.
- View appointments, import/delivery issues and notification states on the same page.
- Detect conflicting edits from multiple browser tabs instead of silently overwriting newer changes. Preserve an audit history.

Retain the ten existing statuses: New, Needs Information, Ready to Schedule, Scheduled, In Progress, On Hold, Completed, Canceled, Declined, Duplicate. Priorities remain Normal, Soon and Urgent. Scheduling decisions and committed dates remain under Mike's control.

## Confirmation and emails

After a successful transaction, show the permanent request ID, connected project when known, current status, position and requests ahead. For example: **Request PHD-00026 — Connected to project PHD-00023 — Position 10 — 9 active requests ahead**. Only actual values for the committed work are shown. Unlinked work receives its own valid reference and position immediately.

Store the submission-time count snapshot so the on-screen confirmation and client receipt agree. Send one client receipt and one owner notification per submission through durable notification delivery. The receipt includes the ID, position snapshot and secure tracking action. Mike's notice includes submission type, client contact information, billing/project addresses when provided, full answers, file links and related-project information or the linking flag.

Email failures do not undo saved work. Recover safely pending sends without duplicates; ambiguous send outcomes require reconciliation. Historical imports do not trigger new receipts. Submission does not promise acceptance, price, a start date or waiting duration. Booking does not create an extra work item or change queue priority/order or design scheduling.

## Current position lookup

The client chooses Track My Project and enters the ID from their receipt. Reuse passwordless verified email access and check authorization for that request/project. A secure link in the receipt can avoid typing the ID again. Recommended access rule: the sequential public ID is a reference, not an access credential; guessing or obtaining an ID must not disclose another client's records.

Show the client's own request ID, project label, client-facing status, position, requests ahead and the time checked. If several requests belong to the project, display each request's status and individual position rather than inventing one position for the whole project. Highlight the reference entered. A completed original request can still show a connected active update. Closed items have no active queue position.

Compute positions from the current database at lookup time, not from the old email snapshot. Initially retain the working count rules: owner-controlled Queue Order, then received time and stable ID; exclude Completed, Canceled, Declined and Duplicate; include Needs Information, On Hold and In Progress. An active item's position is requests ahead plus one. Priority changes alone do not silently reorder work. Explain that position can change and is not a guaranteed wait time.

Keep other clients' names, billing information, internal notes, files and the full queue private. Use server-side object-level authorization for tracking, linking, updates and files. See [OWASP object access guidance](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html).

## Database and website architecture

Introduce a focused Django intake domain with a shared processing service for website submissions, owner-created work and any retained Google-form submissions. Core records are **Client Access**, **Project**, **Work Item**, **Submission and Answers**, **Attachment**, **Appointment**, **Notification Delivery**, and **Audit/Import Records**. Project grouping and individual queue positions are separate relationships.

A Work Item contains internal UUID, public reference, project link, received time, contact snapshot, billing/project address snapshots when supplied, service, description, status, priority, queue order, estimates, requested and committed dates, internal notes and legacy identifiers. Preserve every payload and file relationship in its Submission history. Use unique idempotency keys, transaction-safe reference allocation and queue changes, and database constraints. Account for every existing Master Queue column and useful ledger field before importing.

Reuse the site's templates, form infrastructure, private storage integration and configured email backend where suitable. The existing Get Started handler saves `ProjectInquiry` and uploads and sends notifications via signals; consolidate its notifications into the shared process to prevent duplicate mail. Preserve historical website inquiries and attachments without silently creating active work from unrelated old records. Store full names directly and introduce explicit billing/project fields rather than guessing the meaning of historical optional address fields.

Django is the sole authoritative queue writer after cutover. Google Sheets becomes a preserved archive or export, not a second editable main queue. Background jobs handle notifications, calendar synchronization and a temporary legacy-form bridge if retained. Test concurrency using the production database's transaction behavior.

## Migration and cutover

1. Build and test in isolated data while the current Google Forms and Sheet continue operating.
2. Back up the queue and supporting ledgers. Import active and closed work; preserve UUIDs, PHD references, queue order, statuses, dates, notes, submission identities, complete answers and file references. Do not invent historical billing addresses. Import separate legacy work items independently and group only on verified evidence or owner confirmation.
3. Reconcile counts, individual records, file access, reference uniqueness and active queue positions. No migration emails are sent.
4. Establish a controlled cutover, capture submissions since the baseline import, and initialize the public-reference sequence from the greatest issued high-water mark. Preserve skipped numbers and prevent reuse.
5. Switch intake and owner management to Django. If old Google-form links remain usable, forward their responses through authenticated server-to-server delivery with replay protection; stop their previous queue allocation and receipt sending. Otherwise retire their responder paths with a clear link to the unified form. Verify the chosen policy before launch.
6. Verify the actual intake-to-queue-to-email path, current-position lookup and controlled booking before changing public website links. Rollback reconciles post-cutover submissions before changing the queue writer again.

The user subsequently said no PowerShell helper is needed after moving the database to Render. Do not add one unless requested again. This scope revision changes no live website, Google queue records or calendar settings. Stage 2 intake and stage 3 booking implementation details are recorded in their corresponding documents; live connection and cutover remain separate steps.

## Appointment booking

| Rule | Agreed behavior |
| --- | --- |
| Meeting duration | 30 minutes advertised to the client |
| Buffer | 30 minutes after every meeting, allowing an hour-long meeting |
| Available days | Tuesday through Friday |
| Available hours | 10 AM to 2 PM, America/New_York, including daylight saving changes |
| Latest start | 1 PM; meeting and buffer both fit inside the window |
| Daily limit | Two total client appointments, combining new and existing clients |
| Advance notice | At least 24 hours |
| Client limit | One upcoming appointment per verified client |
| Purpose | Required: What would you like to discuss? |
| Conflicts | Busy calendar events and reserved buffers make times unavailable |

Use passwordless email sign-in and server-checked eligibility. Existing clients book directly without another submission; new customers receive introductory booking access after intake and email verification. Mike grants or revokes ongoing access once, independently of a specific item's Completed status.

Django controls reservations, connected to Google Calendar for availability and events. Retire or restrict the old public scheduling route so it cannot bypass access/capacity limits. Use stable local appointment and Google event IDs, protect concurrent reservations, and reconcile failures before confirming bookings. Cancellation releases future capacity; rescheduling checks every limit again. Optional project/request references travel with the appointment automatically.

Google documents public booking pages and available controls in [sharing appointment schedules](https://support.google.com/calendar/answer/10733297) and [creating appointment schedules](https://support.google.com/calendar/answer/10729749). Client eligibility and per-client limits are requirements for Django, not protections assumed to be enforced by the public Google booking page.

## Acceptance checks

- One public link switches and validates both form paths correctly. New requires all twelve contact/address fields; Update requires the four contact fields plus relevant work and project context, without requiring an ID. Homeowner guidance, address copying and uploads work on phones and computers.
- Every distinct update has its own new ID and queue position and can share a project's history. ID lookup, verified client project selection and name/address matching work without linking the wrong project. Ambiguous work is still queued immediately and can be linked from the same management page. Completed projects accept fresh queued updates without reopening old work.
- Retried submissions do not duplicate work or mail. Submitted answers/files and contact snapshots are preserved. One private queue supports all daily actions, linking, filters, history, downloads and conflict handling.
- Confirmation and receipts use the same submission-time snapshot; tracking reflects later completions/reordering and shows separate positions for related work. Guessed IDs, expired links and another client's update/file requests cannot reveal or alter their data.
- Import preserves all scoped data and references without emails; final cutover has one queue writer, no lost late submissions and no reused references.
- Booking enforces every agreed day/time/buffer/notice/client/daily rule. Reject Monday bookings, 1:30 PM starts, third daily appointments, busy conflicts and concurrent reservations for the same interval. Cancellation/rescheduling and daylight saving time behave correctly.
- Upload/email/calendar failures preserve saved work and recover appropriately; no unsaved request or unconfirmed appointment is presented as successful.
