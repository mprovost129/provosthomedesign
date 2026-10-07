# Client intake — stage 2, October 7, 2026

The existing Django site now contains one public New Submission / Update form, private document handling, automatic database queue entry, durable client/owner receipt delivery, and passwordless project tracking. These features are enabled in the isolated local preview and **disabled by default in production**. This stage does not migrate the live Sheet, deploy to Render, enable real emails, or change Google Calendar.

## Client and owner workflow

1. `/submit-work/` lets anyone submit new work or an update. No account, staff review or email verification is required to enter the queue.
2. Submission atomically saves a separate request ID, initial position, immutable answers, private attachment references and two email records. Retrying the same form does not duplicate the work or receipts.
3. The browser displays the ID and position immediately. Background delivery emails a receipt to the client and every captured answer, private file links and queue link to the owner. An email failure never removes the work.
4. The client's receipt includes a one-use sign-in link. `/track-work/` also accepts email and an optional request ID to request a new link. Email verification protects client details; guessing an ID cannot expose a project or its documents.
5. Once verified, a client sees current positions and project history. Updates get their own IDs/positions. They can select an authorized project or use its ID, exact name or full address. An update submitted before verification still queues immediately; verification can subsequently connect an unambiguous match.
6. The owner manages everything on `/work-queue/`: status, order, notes, dates, files, linking, project access and email exceptions. Completing work removes it from Active Work automatically while preserving history.

New-project ownership is established from the original submission's email claim. Anonymous updates never grant access to an existing project merely because an ID/name/address matches. Unmatched or ambiguous updates remain visible for owner linking. Explicit revocations remain effective; changing a contact email does not silently transfer project access. Staff-created historical submissions receive an immutable owner-email snapshot through migration 0004, but historical shared-project access is not automatically granted.

## Exact questions and data mapping

All client answers are retained in `Submission.answers` with their displayed labels. Common searchable queue values are also copied to `WorkItem`; canonical project values are stored on `WorkProject`. Internal priority, status, estimates, scheduling, order and internal notes are never client form fields.

| Question | Required | Mapping |
| --- | --- | --- |
| Is this a new submission or an update? | Yes | `WorkItem.kind` |
| Full name | Both | `contact_full_name` |
| Company | Both; enter Homeowner if no company | `company` |
| Email address | Both | `contact_email`, immutable `Submission.owner_email` |
| Phone number | Both | `contact_phone` |
| Billing street address / city / state / ZIP code | New only; all four | `billing_street`, `billing_city`, `billing_state`, `billing_zip` |
| Project street address or lot / city / state / ZIP code | New only; all four | `project_street`, `project_city`, `project_state`, `project_zip`; canonical project address |
| Project address is the same as billing address | Optional New | Copies address fields; explicit edits remain authoritative |
| Project name, if you use one | Optional New | `project_name`; canonical project name |
| What do you need help with? | New | `service_needed` |
| Briefly describe what you would like to do. | New | `description` |
| Choose one of your projects | Optional Update; verified email only | Authorized `project` relationship |
| Project ID, if you have it | Optional Update | Captured answer; authorized link resolution |
| Project name or full address | Update if no picker/ID | `project_context`; exact authorized link resolution |
| Please describe the files, update, or requested change. | Update | `description` |
| What are you sending? | Update; or when files are uploaded | Multiple choices in answer snapshot |
| Choose documents or pictures | Optional Both | Private `Attachment` records associated with the submission |
| File or folder link, if you prefer | Optional Both; HTTPS | `file_link` |
| Requested response or drawing date, if applicable | Optional Both | `requested_deadline` |
| Approximate size or scope, if known | Optional New | `approximate_size` |
| What is your preferred timeframe for design work? | Optional New | `timeframe` |
| Plan number, if you are asking about a particular plan | Optional Both | Answer snapshot |
| How would you prefer us to contact you? | Optional Both | Answer snapshot |
| How did you hear about us? | Optional New | Answer snapshot |
| Anything else we should know? | Optional Both | Answer snapshot; separate from internal notes |
| Home preferences, rooms or design goals | Optional New, collapsed details | Answer snapshot |
| Changes you would like made to an existing plan | Optional New, collapsed details | Answer snapshot |
| Framing scope or areas needing coordination | Optional New, collapsed details | Answer snapshot |
| Known site, HOA or permit constraints | Optional New, collapsed details | Answer snapshot |
| Other project contacts, if useful | Optional New, collapsed details | Answer snapshot |

Service options: New custom home; Addition or renovation; ADU or garage; Stock-plan modification; Framing plans; Other / not sure.

Attachment categories: Plans or survey; Photos or sketches; Answers or corrections; Revision request; Other. Multiple selections are supported. Timeframes: As soon as available; 1–3 months; 3–6 months; More than 6 months; Just exploring. Preferred contact: No preference; Email; Phone.

Inactive path fields are ignored server-side. Neither path requires a document; a client can submit a description first or provide an HTTPS file/folder link for another file format.

## Private uploads

The agreed allowance is **10 files per submission, 100 MB each**, implemented as 100 × 1024 × 1024 bytes per file. Accepted: PDF, JPG/JPEG, PNG, WebP, DOCX and XLSX. Other formats can use the HTTPS link field.

Production uploads go directly from the browser to a **separate private S3 bucket**, using a signed POST restricted to one key, exact size and content type for 15 minutes. Django verifies actual content and seals the validated object under a different private key. Conditional S3 reads/copies prevent a still-valid upload credential from replacing a committed document. Session-bound tickets and idempotent upload UUIDs prevent another browser from attaching/replacing files and prevent ordinary retries from creating duplicate tickets. Queue commit refuses incomplete uploads.

PDF signature/end marker, actual image decoding and Office package checks reject basic mismatches, malformed files, macros, encrypted Office packages and decompression hazards. These structural checks do not claim antivirus scanning. Downloads require authorized staff or verified client access and are served as attachments with no-store/nosniff protections. Local preview files are kept outside public media under `queue-private-uploads/`.

Expired drafts and staging copies are removed by `cleanup_queue_uploads`. Cleanup waits 20 minutes beyond draft expiry, covering the last possible signed upload permission. Submitted sealed attachments are retained. Keep staging-object lifecycle cleanup as a secondary operational safeguard, with no blanket expiry on submitted `intake/ready/` objects.

Implementation guidance: [Django upload handling](https://docs.djangoproject.com/en/5.2/topics/http/file-uploads/), [django-storages S3 configuration](https://django-storages.readthedocs.io/en/stable/backends/amazon-S3.html), and [OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

## Production configuration and launch sequence

Do not expose an empty allocator before importing existing IDs/order. Follow the foundation document for the Sheet import and high-water mark reconciliation. Deploy the schema with both feature flags off, reconcile historical records/files, then enable only after the production checks below.

Required settings:

```text
WORK_QUEUE_ENABLED=False
WORK_INTAKE_ENABLED=False
INTAKE_DIRECT_UPLOADS=True
INTAKE_PUBLIC_BASE_URL=https://www.provosthomedesign.com
INTAKE_OWNER_EMAIL=mike@provosthomedesign.com
INTAKE_PRIVATE_BUCKET=<separate-private-bucket-name>
AWS_S3_REGION_NAME=<bucket-region>
```

Use the existing credential environment securely; never check secrets into source. The application's IAM principal needs bucket `GetBucketPublicAccessBlock` and object read/write/delete access restricted to the private bucket's `intake/` prefix. Enable all four S3 Block Public Access settings, encryption at rest, and a CORS rule allowing POST from the exact public site origin with Content-Type/required signing headers. Do not grant public read or reuse the website's public media bucket. Verify the signed POST, validation/sealing, download and cleanup against the actual bucket before launch; this session used local storage and mocked S3 calls only.

Use a real outbound email backend and verified sender. Console, file and memory email backends are for development; they do not deliver client receipts. Run the existing database-cache creation step if Redis is not configured, because rate limits must be shared across production workers. Keep HTTPS, secure cookies, CSRF protection and correct site origin settings.

Apply queue migrations 0001–0005 using the normal deployment migration process. Configure recurring jobs/worker execution with the same database and email/storage environment:

```text
python manage.py process_queue_emails --limit 100
python manage.py cleanup_queue_uploads
```

Run email delivery frequently (for example once a minute) and cleanup hourly. The email worker serializes claims and commits a sending state before calling the provider. Successful messages are not resent; failed/unknown outcomes are visible on the queue. Stale in-flight mail is held as Unknown rather than blindly repeated. Only retry Unknown after provider logs confirm no delivery; the queue requires explicit confirmation. Delayed or explicitly retried receipt emails receive a fresh sign-in link. Sent email bodies are cleared from the outbox to avoid retaining bearer links. Monitor failed/unknown counts and worker health; do not treat a queued receipt as proof of external delivery.

Email links expire after 24 hours and can be used once; verified browser access lasts seven days. GET previews do not consume a link. Tokens travel in the URL fragment and are confirmed by a CSRF-protected POST, then removed from browser history. Intake and verification endpoints have rate limits, browser-bound form tokens and a honeypot. No analytics are included on client/queue pages. Project IDs remain useful lookup references but are not passwords.

Before launch: run the dedicated local PostgreSQL concurrency checks; test actual S3 CORS/policies/credentials and real client/owner email delivery; verify the imported reference/order high-water mark and private document ownership; finish controlled appointment booking; rehearse feature-flag cutover/rollback. The Google Forms/Sheet remain authoritative until cutover is verified, and the public Google booking page must not bypass the agreed booking limits.

## Validation

The safe `config.settings_queue_dev` environment does not load `.env` or contact Render and uses local fictitious records/in-memory email. Regression commands:

```text
python manage.py test pages plans workqueue --settings=config.settings_queue_dev
python manage.py makemigrations --check --dry-run --settings=config.settings_queue_dev
python manage.py check --settings=config.settings_queue_dev
node --check static/js/client-intake.js
node --check static/js/client-access.js
```

The browser walkthrough uploaded a fictitious PDF, selected multiple attachment categories, created a new request, confirmed private email access, and created a linked update with a separate ID/position. Both appeared immediately on the owner queue and client tracking page. The form uses a same-origin referrer policy; ordinary browser submission succeeds while null-origin requests remain rejected. Native CSRF protection was not relaxed.

Final automated results: 154 tests discovered, 152 passed and two PostgreSQL-only concurrency tests skipped on SQLite; a local PostgreSQL engine was unavailable. Django configuration/migration consistency checks and both JavaScript syntax checks passed. Actual S3 and real outbound email integration remain unverified. Booking and historical Sheet import/cutover are subsequent stages. No PowerShell deployment helper was added, following the user's latest preference.
