# Daily queue tools

The Work Queue remains the single place to manage requests.

## Needs attention

The attention counts cover the entire queue, independently of search and pagination:

- Overdue: active requests with a promised due date before today.
- Follow-ups due: active requests with a follow-up date today or earlier.
- Responses to review: requests with verified client responses that have not been marked reviewed, including closed work.
- Waiting for information: active requests marked Needs Information with no unreviewed response.
- Confirm project: active updates without a connected project.
- Email issues: requests with failed or uncertain receipt, information-request or milestone delivery, including closed work. A request is counted once even if several emails need attention.

Click a count to open that view with All work selected. The Attention filter can be combined with the other filters. Queue positions always refer to the full active queue. Overdue and follow-up badges appear on affected rows. Dates use the site's America/New_York timezone. Attention counts are updated when the page is loaded; incoming work is announced separately without replacing the page.

## Files

Open the Files (n) button in a row's Project / work column. Empty requests show 0 files. Links open in a separate tab so the queue and unsaved edits remain available. These are files submitted with that specific request. Related requests retain their own files and history. Existing private download checks and staff-only access to original Google Drive files are unchanged. Files are not bundled into large server-generated ZIP archives.

## Request information

1. Open Manage for an active request, then Request more information.
2. Use a template or write your own client message. Optionally set an internal follow-up date.
3. Click Preview email. Check the recipient, message and update link.
4. Click Send email & mark Needs Information in the preview.

Only that explicit send queues an information-request email. Previewing does not send messages; status saves send a milestone email only when explicitly selected. The recipient is the request's submitted email address; internal notes and the follow-up date are excluded. The existing background worker delivers the email, typically on its next run. The request keeps its queue order, becomes Needs Information, and records the message, requesting staff member and delivery status. A blank follow-up date clears an existing reminder; set a date if you want one retained.

Clients receive the same public update form with their request ID prefilled. This convenience does not authorize access or disclose project information. They use their original email address; existing email verification controls project linking. Updates retain their own queue positions. Sending an information request does not create another work item, and an incoming update does not automatically close the earlier request.

## Response received

Once a client verifies the submitting email, a matching update is connected to the information request. Already verified clients are connected during submission. The original request shows **Response received—review needed** and appears under **Responses to review**. Open **Review client responses** directly in its row to read the update, open its attachments, and mark it reviewed. The update remains a separate queue entry with its own ID and position.

Matching uses the prior request/reference, the verified submitter and existing project access. A project name/address is sufficient only when one request on that project is clearly waiting for information. Ambiguous matches are left alone; the update still appears in the queue. Updates submitted before the question, revoked access and different email addresses do not create response flags. Ordinary revisions submitted after an exchange was reviewed do not reopen it; a new information request starts another exchange.

Marking a response reviewed records who reviewed it and when. It clears that response's notice without changing job status, dates or queue order. Change the status separately when appropriate. If the status remains Needs Information after review, the original returns to Waiting for information. Multiple responses are reviewed individually. The message history retains links to responses and review records. Staff need the existing change-work permission to mark responses reviewed. No additional email or service is introduced.

Email previews expire after 30 minutes. Concurrent changes require another preview. Repeating the same send does not create another email or reset later status changes. Pending/sent/failed/uncertain delivery is shown under the information-request history. Failed mail can be retried; uncertain delivery requires checking the mail provider and confirming it did not arrive before retrying.

## Deployment and validation

Migration 0013 adds milestone send/skip history without changing existing jobs or sending historical milestone emails. The new arrival endpoint uses the existing committed reference allocator; no polling service or new charge is required.

Migrations 0011 and 0012 add information-request and response records without modifying existing requests, files, receipts or their references. Existing queued updates can be matched the next time their client verifies their email; no production backfill is run automatically. The existing notification outbox and background worker need no new environment variables or recurring services. Deploy the migration before serving the new queue code. Validation uses isolated database, storage and in-memory email settings; it does not send test messages to clients.

## Client submission receipts

Every new website submission and update receives a receipt containing its request ID, submission date/time, position when submitted, all completed public form answers, and an uploaded-document list. Each uploaded file includes its original name, size, selected categories and a private download link. A submitted external file/folder link is included with the answers. Empty optional questions are omitted. Internal notes, staff dates/priorities and technical tokens are not included.

Documents are linked rather than attached, allowing large plans to be retrieved through the existing verified-email access. Clients first use the secure sign-in link, then a document link. That sign-in link expires after 24 hours; the receipt also includes the permanent Track My Project page where they can request a fresh one. Document URLs grant no new access and are never public S3 URLs.

The receipt captures the accepted submission. Later staff edits, queue moves, project renames and subsequent uploads do not rewrite that email. Each update has its own request ID and lists only its own answers/files. Retries keep the existing idempotency and delivery safeguards. This applies to new submissions after deployment; historical receipts are not resent. No database migration, new email service or setting is required.

## Client milestone emails

Both Save status and Save request offer a client-email choice, defaulting to Skip email. Select Send milestone email when changing to In Progress or Completed to queue a start/completion notice. The full request form labels this Send when work starts or finishes. Selecting Send for another status, saving an unchanged status, or editing notes/dates/order does not send anything. Reopening work and later starting/completing it again is a new milestone and can be sent explicitly.

Messages include only the client name, request ID, project name, the selected milestone and a tracking link. Internal notes, phone/billing data, staff reminders and queue positions are excluded. Clients sign in using their submitting email. Completion messages do not claim that files are attached or delivered. Client milestone emails in Manage shows the saved send/skip decision, staff member, recipient and delivery state. Failed/uncertain deliveries also appear under Email issues and use the existing guarded retry flow. Repeated/concurrent saves cannot create duplicate messages for one transition. Status and outbox writes commit together; if email preparation fails, choose Skip email to save without a message. Existing background jobs deliver queued messages without new services or settings.

## Arrange the queue

Click **Arrange queue** above the work table. The panel includes every active request, regardless of filters or pagination. Drag the left-hand handle, use the up/down buttons, or focus a handle and use the arrow keys. Click **Save order** to apply the order and refresh the current view. **Cancel** discards the arrangement. Clients see the saved positions on their next tracking check; no position-change emails are sent.

Updates remain separate work items connected to their projects. Closed work stays outside the arranger; original receipt positions, request IDs, files, statuses and internal notes are preserved. Newly submitted work still appends to the active queue. Existing Queue order fields remain available for manual edits.

Save or undo other form drafts before arranging. A pending arrangement also protects against refreshing or saving another form and losing the order draft. If a new request arrives or another tab edits the queue before saving, the server rejects the stale arrangement without changing any jobs. Use **Reload order**, review the current jobs, and arrange again. A timeout or lost response also requires reloading the order before retrying, since the save may have completed.

The staff-only `/work-queue/order/` endpoint requires both view and change permissions, CSRF on POST, and a signed snapshot (two-hour lifetime). Saves validate the complete active set under the existing queue-state lock, update item versions, and record each affected job's previous/new position and order in Change history. No migration, new service or environment setting is required.

## Arrival notice and drafts

The staff page checks for arrivals every 30 seconds while visible and when returning to the tab. A notice such as 2 new requests counts accepted new requests and updates across the whole queue, including requests outside current filters and work entered by staff. Edits and repeated submissions do not increase it. Refresh queue reloads the current URL, keeping filters and resetting the notice. It never refreshes automatically.

While a form has unsaved edits, Refresh queue is disabled with an explanation. Saving another form is also blocked until the other drafts are saved or undone, and normal navigation/reload gets the browser's unsaved-changes prompt. Applying an information template is a draft change too. No drafts are stored in browser storage or sent by polling. Network/session failures show that arrival checking is unavailable; existing rows and drafts stay intact. The signed, staff-only poll returns only a count, uses no-store caching, and expires after seven days; refresh the queue when ready to resume.

## Recover an unfinished client form

Public submission forms save partial answers automatically after editing. The saved form is not a submitted request: it has no queue position and sends no email. On returning in the same browser, choose **Resume saved form**. Each successful save keeps the draft for seven days. Up to five drafts can be retained per browser session. Clearing cookies, using another browser/device, or ending the session can make recovery unavailable. On shared devices, choose **Discard saved form and start fresh**.

Answers are stored in the database, bound to the browser session; they are not stored in localStorage or placed in the resume URL. Terms acceptance and CAPTCHA are never saved. Final submission still validates all required answers and uploads. Unsubmitted files expire after 24 hours. Recovery lists expired filenames so the client can upload them again or explicitly acknowledge proceeding without them. Saved answers are removed when a submission commits, and expired drafts are removed by the existing cleanup worker. Conflicting edits from multiple tabs require resuming the latest saved draft.

## Submission history and receipts

After email verification, **Track my project** shows the original submitter's full saved public answers, uploaded file list, and **Download submission receipt** for each request. The downloaded receipt is a text file. Later staff edits do not rewrite the submitted record. Updates retain separate request IDs and places in line. Shared project access does not expose another submitter's billing or full form answers; original archived files without private storage keys remain identified as archive records.

## Deliver completed files

Open **Manage → Deliver completed files** from the request row. It opens a separate tab to protect unfinished queue edits. Upload up to ten completed files, 100 MB each, and enter an optional client message. Choose **Preview delivery email**, review the recipient, message and filenames, then **Send completed files**. Internal notes are excluded. The client receives secure file links and sees the release under **Completed files delivered by Provost Home Design**. Original submissions stay separate. Sending files does not change the request status; save Completed separately when appropriate.

Delivery requires staff view/change permission and existing client access to that request. Previews expire after 30 minutes; changed recipients, edits or files require another preview. Duplicate confirmation does not send another email. Email delivery states and guarded retries appear in the queue's email history/issues. Downloads use the existing private storage and verified access; no public S3 URLs or email file attachments are introduced.

## One optional information reminder

In **Request more information**, leave **Send the client one reminder on** blank for no reminder, or choose a future date. The date appears in the email preview. The existing worker queues one reminder only if the original question was emailed successfully and the request is still waiting on that same client. Verified responses, status/contact changes, revoked access or a newer question suppress it. Eligibility is checked again before sending. A message already being handed to the mail provider cannot be recalled. Reminder state appears with the information-request history and email issues. Reminders do not change queue order or status.

## System health and independent alerts

Expand **System health** above the queue for the last successful worker run and actionable issues. Checks cover background jobs without success for 15 minutes, delayed/failed/uncertain mail and appointment exceptions. The public `/queue-health/` endpoint returns only a minimal health signal, never customer details, filenames, counts, credentials or provider errors.

The existing Google Apps Script project can independently check this endpoint every five minutes. Its source and activation instructions are in `ops/WebsiteQueueMonitor.gs` and `docs/QUEUE_RECOVERY_AND_MONITORING.md`. Two consecutive failed checks produce one owner alert, and recovery produces one recovery email. Activation requires the additional external-request permission and running `enableWebsiteQueueMonitor` once. No new paid Render service is required. Monitoring is not active merely because the script file exists.

## Upgrade deployment

Apply migrations 0014–0016 before serving the upgraded web and worker code. They add draft, completed-file delivery, reminder and heartbeat records; they do not change existing jobs, submission IDs, original answers or attachments. The existing `run_queue_jobs` schedule handles delivery, reminders, cleanup and heartbeat updates. No new Django environment variables are required. Validation includes 258 isolated PostgreSQL checks, draft JavaScript checks and monitor behavior checks. The recovery rehearsal and verified cloud settings are documented separately.
