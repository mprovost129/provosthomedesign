# Editing work requests from the queue

Choose Edit details on a row. On a phone, open Actions first. This opens Manage and jumps directly to the editor. Fields are grouped as Project details; Work requested; Contact and billing for this request; Queue, dates and private notes; and Source and original reference.

Staff with change-work permission can correct the project name/address/connection, request type, project reference/context, service and description, contact name/company/email/phone, billing address, queue settings, dates, internal notes and source/reference. Request IDs, received timestamps, original submitted answers and attachment records are retained.

Project name and address changes update the connected project and its linked queue rows. Every linked row receives a version change so an older open editor cannot silently overwrite the correction. Where the connected project lacks an address, the editor starts from the address already provided on the request rather than clearing it on save. Reconnecting a request to another project preserves the target project's existing defaults unless staff explicitly change them.

Contact and billing edits apply to this request. They do not rewrite the saved CRM profile, reassign contacts, grant portal access or change the ownership of original submissions. Use Client record for shared client-profile changes and the existing access controls for access changes. Corrected request email addresses are validated, normalized and used by future request-email actions. Previously prepared notifications keep their saved recipient and content.

Save request uses the existing transaction lock, optimistic version checks, validation and audit history. A project-wide correction invalidates all linked editors. Validation failures roll back project and request changes together. Older open forms preserve newly introduced fields that were not posted. Imported requests may retain blank contact details while staff correct other fields.

No message is sent merely because details change. The existing explicit milestone-email choice applies only when status changes to In Progress or Completed. Original submissions and prior sent documents are not rewritten by a queue correction. Editing a project in the queue does not rename an existing Dropbox folder or FreshBooks project.
