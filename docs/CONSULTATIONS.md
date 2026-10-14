# Initial consultations

The public booking page links to /request-consultation/. The short form collects full name, email, phone and a project description, with terms consent, CSRF, session-bound submission tokens, reCAPTCHA action work_consultation, and bounded IP/email limits. Requests do not reserve a calendar slot or create a work-queue entry.

An unverified request queues only an email verification link. The existing fragment-token confirmation flow verifies the address before creating/reusing a CRM contact and notifying the owner. Verification expires in 24 hours; the original requesting browser can request another email. GET requests and email scanners do not verify or approve a request. Multiple submissions/verification tokens for the same pending email cannot create duplicate CRM contacts or owner notices.

New contacts become CRM inquiries with booking_requires_approval=True. Existing ordinary CRM contacts retain their automatic booking eligibility. An existing contact is reused by exact verified email without changing its name, phone or other details. Explicit booking blocks survive verification.

Pending, verified requests appear under Work queue -> Client appointments -> Initial consultation requests. Staff with change_bookingaccess and normal queue access can approve or decline. Approval is an audited transaction that enables booking, clears the new contact's first-approval requirement and queues a single email with a booking sign-in link. Decline queues a courteous email and does not enable booking. Replayed decision POSTs do not send duplicate emails. Contact changes require review before approval. Archived or changed contacts can still be declined.

After approval, the existing appointment flow handles meeting type, availability, Google Calendar/Meet, buffers and confirmations. Consultation approval never grants access to private projects or documents. No real mail is sent by tests or local previews. Outbox delivery uses the existing scheduled worker and Gmail configuration.

Migration 0039 adds the contact approval flag (persistent False database default for old writers), the consult email-token destination, and ConsultationRequest. Apply migrations before the new web/worker code starts. New tables hold request state and references to the durable verification, owner, receipt and decision email records.
