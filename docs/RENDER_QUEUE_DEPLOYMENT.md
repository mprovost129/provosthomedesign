# Render queue deployment — October 7, 2026

## Deployed foundation

Commit `d786acb9c0526fa137de3d7511a870e734238211` was pushed to `mprovost129/provosthomedesign` on `main`. Render automatically deployed it to `provosthomedesign` (`srv-d8cps8urnols739r24sg`); deployment `dep-db37a0e0tbcc739650ng` succeeded. Logs confirm queue migrations 0001–0009 applied successfully, static files collected, and all three web workers started. The main site is `https://www.provosthomedesign.com`; the same service also serves the separate web-design subdomain.

Public intake, queue and booking remain disabled by default. Existing Google intake remains authoritative. No historical Sheet data has been imported, no client emails have been sent by the new app, and no live appointments have been created by this deployment. Unrelated partner-document edits and local output/tmp files were excluded from the commit.

## Scheduled job configuration

Use one finite Render Cron Job for the three durable tasks, rather than a recurring process inside Gunicorn. `run_queue_jobs` dispatches calendar synchronization, then email delivery, then expired-upload cleanup. The job does no work while public intake is disabled; calendar work additionally requires booking enabled. A task failure does not prevent the other tasks from running, and the overall job exits unsuccessfully without logging provider responses or credentials. Render serializes runs of a single cron service.

| Field | Value |
| --- | --- |
| Name | `phd-queue-jobs` |
| Project/environment | Provost Home Design / Production |
| Repository/branch | `mprovost129/provosthomedesign` / `main` |
| Region | Ohio (US East), matching the website and database |
| Runtime | Python 3 |
| Build | `pip install -r requirements.txt` |
| Command | `python manage.py run_queue_jobs --appointment-limit 50 --email-limit 100` |
| Schedule | `* * * * *` (every minute, UTC) |
| Compute | Starter, subject to owner approval of the displayed charges |

Supply the same private database, Django secret, email, private S3 and Google Calendar configuration as the web application, with matching feature flags. Do not paste credentials into command text, GitHub, documents or logs. The build must not run migrations; the web deployment owns migration execution. Keep the job disabled/suspended until dependencies are checked, or leave all feature flags false so its invocation safely does no work. Routine expired-upload cleanup checks are safe each minute; submitted files remain retained.

Four focused tests passed for disabled-feature isolation, intake-only dispatch, continued email/cleanup dispatch after calendar failure, redacted error reporting and validated task limits. Prior full app validation: 187 passed, four PostgreSQL concurrency checks skipped because the local PostgreSQL engine was unavailable.

## Before enabling clients

1. Verify the Calendar connection from the deployed runtime and test a controlled real meeting, its buffer and cancellation.
2. Configure a separate private S3 bucket and validate upload, sealing, authorized download and expired-draft cleanup.
3. Verify real owner/client email delivery and the shared production rate-limit cache.
4. Complete PostgreSQL concurrency checks against an isolated test database.
5. Reconcile/import existing Sheet references, queue order, statuses and private attachment ownership; set the allocator above all issued references. Preserve existing Google appointments and daily capacity.
6. Verify the scheduled job, retire/restrict the old Google booking path, and perform a coordinated feature-flag/public-link cutover.

Rollback before cutover: disable the three feature flags and the new job, and redeploy the prior app if necessary. Retain the additive queue tables; do not reverse/drop them as part of an ordinary website rollback. After cutover, reconcile new submissions and calendar reservations before any rollback.

Render references: [Cron jobs and billing](https://render.com/docs/cronjobs), [Deployments](https://render.com/docs/deploys). A Cron Job has a $1 monthly minimum and usage charges based on active runtime; confirm the current dashboard rate before creation.

## Deployment and job verification completed

The background runner commit `3f426a89859c8375d54117a0cdad3acce5f88883` was pushed to `main` and successfully deployed to the web service. Read-only production checks confirmed all three feature flags false, zero queue/appointment records, reference allocator zero, and the existing shared `django_cache` table. Both main and web-design homepages returned HTTP 200; the disabled intake, queue and booking routes returned HTTP 404.

The owner approved creation of the scheduled job with a budget of up to $8/month before tax, including reuse of the app's private database/email/S3/Calendar configuration. Render Cron Job `phd-queue-jobs` (`crn-db37gb3ncjis73enhuo0`) was created in Production/Ohio with the configuration above. Its displayed compute rate was **$0.00016 per active minute**, 0.5 CPU and 512 MB RAM. Its first build (`bld-db37gbbncjis73eni00g`) succeeded from the runner commit. Nineteen necessary existing app settings were copied privately, plus five explicit queue flags/origin/owner settings; unrelated reCAPTCHA/analytics settings were not copied. No plaintext credentials were written to files, source, documents or job commands.

A scheduled run finished successfully and logged **Queue features are disabled; no background tasks ran.** The verified job was then **suspended pending client launch**, retaining its configuration. Render displayed **You are not billed for suspended services.** Resume it only after updating its private storage settings and feature flags to match the web service during the coordinated cutover. Remember that environment values are private copies, not a linked environment group; later credential rotations or configuration changes must be applied to both processes.

Google Calendar identity/availability was verified inside the deployed web runtime. A separate controlled test through the actual Calendar adapter saved a real private 30-minute meeting and 30-minute buffer in an unused future interval, retried the same stable event IDs without duplication, verified both busy intervals, canceled and verified removal of both events, and verified an already-absent cancellation retry. The test created no app database records, invitations or external emails. This proves provider writes/deletion; the live public booking/database/email workflow remains gated until the remaining launch requirements above are complete.

Private intake storage is not yet configured; the existing public media bucket has not been reused for client documents. Real receipt delivery, Sheet import/reference/order/file reconciliation, existing-appointment capacity reconciliation and the isolated PostgreSQL concurrency checks remain required before public enablement. The working Google Forms/Sheet and old booking route remain unchanged.

## Private S3 upload storage completed

The owner approved a separate private upload bucket, narrow application access, connection to both Render processes, disposable sample-file testing/deletion, and an initial additional AWS planning budget of $5/month before tax. This is a usage budget, not a hard AWS billing cap or an AWS Budgets resource. Current published us-east-1 Standard storage rate: $0.023/GB-month (100 GB storage alone: $2.30/month), plus requests and transfer. The pricing publication checked was September 28, 2026.

Bucket `phd-intake-private-prod` was created in Northern Virginia (`us-east-1`). All four Block Public Access controls are enabled, ownership is BucketOwnerEnforced with ACLs disabled, and default encryption is SSE-S3/AES256. A bucket policy denies non-TLS requests. CORS permits POST uploads from exactly `https://www.provosthomedesign.com`, with Content-Type and x-amz-* request headers, ETag exposure and a 300-second preflight cache. Versioning remains disabled and no automatic file-expiration lifecycle rule was added; submitted documents have no automatic expiry.

The existing application IAM user `provost-home-design` received a separate inline policy `phd-intake-private-access`: GetBucketPublicAccessBlock for this bucket and GetObject, PutObject and DeleteObject only for `intake/*` in this bucket. The existing `phd-media-policy` and website media bucket were preserved. No access keys were created, rotated or copied during this setup.

`INTAKE_PRIVATE_BUCKET=phd-intake-private-prod` was saved to the web service and scheduled job. The web service applied a configuration-only deployment using its existing app build; deployment `dep-db396ecs728c73boiq7g` was verified Live. The job's saved bucket value was checked explicitly; it remains suspended. All public queue/intake/booking flags remain false.

Real provider verification from the deployed runtime passed for both a valid PDF and PNG: website-origin preflight/upload response headers, denial of a disallowed-origin preflight, rejection of an upload exceeding its signed declared size, application content validation and sealing to a fresh ready-file key, AES256 object encryption, signed download with matching bytes, and anonymous download HTTP 403. The actual upload-policy and finish-upload functions were used with in-memory ticket objects; no queue/database records were written. All four approved temporary staging/ready objects were deleted, and the AWS console subsequently showed zero objects. This verifies the storage adapter and provider; it does not claim a live client HTTP intake/receipt workflow test.

A subsequent read-only production check confirmed zero work items, appointments and pending uploads, and both main and web-design homepages returned HTTP 200. Receipt delivery, historical Sheet import/reference/order/file reconciliation, existing appointment capacity reconciliation, isolated PostgreSQL concurrency checks and coordinated cutover remain before public launch.

Pricing sources: [S3 pricing](https://aws.amazon.com/s3/pricing/), [current us-east-1 price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json).

## Existing Gmail and receipt delivery verified

The queue uses the existing Django SMTP email backend and DEFAULT_FROM_EMAIL settings already used by the website forms. A connection-only check from the deployed Render runtime authenticated successfully to smtp.gmail.com on port 587 with TLS; it sent no messages and did not expose or change credentials.

After owner authorization, two clearly labeled [TEST] messages were sent to the configured business owner address, mike@provosthomedesign.com: a sample client receipt and an owner notification. The deployed queue_receipts function composed both using synthetic answers and explicitly inactive sample links, with only token minting and submission persistence substituted for test purposes. Actual NotificationDelivery records and the actual deliver_pending worker performed SMTP delivery. No real project, submission, queue ID/position or email access token was created; those records and allocator values were checked unchanged.

Both notices were accepted by Gmail, recorded sent with one attempt and a sent-at timestamp, and their stored bodies were cleared by the actual worker. A second worker invocation processed zero notices, confirming no duplicate delivery. The owner explicitly confirmed that both messages arrived on October 7, 2026. Test delivery metadata remains in the outbox audit under batch phd-emailcheck-20261007-6d946d3f76cd4fc3893622542fba71d5; these notices are not live work items and cannot resend automatically.

This verifies receipt composition, durable outbox processing and real delivery to the owner's inbox through the existing Gmail configuration. Full public intake/verification/tracking and booking workflows remain disabled pending historical Sheet import, existing-appointment reconciliation, isolated PostgreSQL concurrency checks and coordinated cutover. The scheduled job remains suspended until launch; both processes already have the same existing email configuration.
