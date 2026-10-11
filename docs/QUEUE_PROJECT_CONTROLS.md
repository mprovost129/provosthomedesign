# Edit, copy and delete queue requests

Each queue row is one request. Linked updates remain separate requests and have
their own place in line. Use the controls beside the request ID.

- **Edit** opens the existing queue form, including project name/address,
  requested work and service. Project name/address changes also update the shared
  project; the other linked forms must reload before saving. Changing the connected
  project alone preserves the destination project's details. Original submitted
  answers and contact snapshots remain unchanged.
- **Copy** opens a review form without saving anything. It starts as a separate
  new project; choose the original connected project when the work belongs there.
  Saving creates a new request ID at the end of the queue for the same client.
  Scheduling, private notes, files and email decisions are not duplicated, and
  no automatic emails are sent. Repeated saves of the same copy form allocate once.
- **Delete** requires the staff Delete permission and confirmation naming the
  selected request. It removes just that row from Active, All, Closed, CRM job lists
  and client tracking. Positions immediately reflect the remaining active rows.
  Other linked requests, bookings, client records and stored files are unchanged.

Choose **Show → Deleted work** to recover a mistaken deletion. Restore requires
Delete and Change permissions. Active restored work appends to the end of the queue;
completed work remains closed. References are never reused. Original receipt
positions stay as recorded. Files and private audit history remain recoverable.

Pending receipt, information, reminder, milestone and file-delivery messages are
canceled on deletion. Restore does not resend them. Messages already handed to the
mail provider cannot be recalled. The worker rechecks deletion before sending.
Deleted original submissions cannot establish new project grants when an old
verification link is used. Existing grants and appointment history are unchanged.

All writes share the queue lock. Edits/deletions/restores check the row version;
copies also have an idempotency token. Drag-and-drop snapshots become invalid when
the active queue changes. Public tracking and downloads exclude deleted requests.
