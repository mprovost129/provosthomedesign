# Daily queue tools

The Work Queue remains the single place to manage requests.

## Needs attention

The attention counts cover the entire queue, independently of search and pagination:

- Overdue: active requests with a promised due date before today.
- Follow-ups due: active requests with a follow-up date today or earlier.
- Responses to review: requests with verified client responses that have not been marked reviewed, including closed work.
- Waiting for information: active requests marked Needs Information with no unreviewed response.
- Confirm project: active updates without a connected project.
- Email issues: requests with failed or uncertain receipt/information-request delivery, including closed work. A request is counted once even if several emails need attention.

Click a count to open that view with All work selected. The Attention filter can be combined with the other filters. Queue positions always refer to the full active queue. Overdue and follow-up badges appear on affected rows. Dates use the site's America/New_York timezone. Counts are updated when the page is loaded; this release does not auto-refresh the queue.

## Files

Open Files (n) in a row's Project / work column. These are files submitted with that specific request. Related requests retain their own files and history. Existing private download checks and staff-only access to original Google Drive files are unchanged. Files are not bundled into large server-generated ZIP archives.

## Request information

1. Open Manage for an active request, then Request more information.
2. Use a template or write your own client message. Optionally set an internal follow-up date.
3. Click Preview email. Check the recipient, message and update link.
4. Click Send email & mark Needs Information in the preview.

Only that explicit send queues an email. Status saves and previewing do not send messages. The recipient is the request's submitted email address; internal notes and the follow-up date are excluded. The existing background worker delivers the email, typically on its next run. The request keeps its queue order, becomes Needs Information, and records the message, requesting staff member and delivery status. A blank follow-up date clears an existing reminder; set a date if you want one retained.

Clients receive the same public update form with their request ID prefilled. This convenience does not authorize access or disclose project information. They use their original email address; existing email verification controls project linking. Updates retain their own queue positions. Sending an information request does not create another work item, and an incoming update does not automatically close the earlier request.

## Response received

Once a client verifies the submitting email, a matching update is connected to the information request. Already verified clients are connected during submission. The original request shows **Response received—review needed** and appears under **Responses to review**. Open **Review client responses** directly in its row to read the update, open its attachments, and mark it reviewed. The update remains a separate queue entry with its own ID and position.

Matching uses the prior request/reference, the verified submitter and existing project access. A project name/address is sufficient only when one request on that project is clearly waiting for information. Ambiguous matches are left alone; the update still appears in the queue. Updates submitted before the question, revoked access and different email addresses do not create response flags. Ordinary revisions submitted after an exchange was reviewed do not reopen it; a new information request starts another exchange.

Marking a response reviewed records who reviewed it and when. It clears that response's notice without changing job status, dates or queue order. Change the status separately when appropriate. If the status remains Needs Information after review, the original returns to Waiting for information. Multiple responses are reviewed individually. The message history retains links to responses and review records. Staff need the existing change-work permission to mark responses reviewed. No additional email or service is introduced.

Email previews expire after 30 minutes. Concurrent changes require another preview. Repeating the same send does not create another email or reset later status changes. Pending/sent/failed/uncertain delivery is shown under the information-request history. Failed mail can be retried; uncertain delivery requires checking the mail provider and confirming it did not arrive before retrying.

## Deployment and validation

Migrations 0011 and 0012 add information-request and response records without modifying existing requests, files, receipts or their references. Existing queued updates can be matched the next time their client verifies their email; no production backfill is run automatically. The existing notification outbox and background worker need no new environment variables or recurring services. Deploy the migration before serving the new queue code. Validation uses isolated database, storage and in-memory email settings; it does not send test messages to clients.
