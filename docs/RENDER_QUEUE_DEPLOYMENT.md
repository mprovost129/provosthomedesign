# Render queue deployment — October 7, 2026

## Deployed foundation

Commit `d786acb9c0526fa137de3d7511a870e734238211` was pushed to `mprovost129/provosthomedesign` on `main`. Render automatically deployed it to `provosthomedesign` (`srv-d8cps8urnols739r24sg`); deployment `dep-db37a0e0tbcc739650ng` succeeded. Logs confirm queue migrations 0001–0009 applied successfully, static files collected, and all three web workers started. The main site is `https://www.provosthomedesign.com`; the same service also serves the separate web-design subdomain.

Public intake, queue and booking remain disabled by default. Existing Google intake remains authoritative. No historical Sheet data has been imported, no client emails have been sent by the new app, and no live appointments have been created by this deployment. Unrelated partner-document edits and local output/tmp files were excluded from the commit.

## Prepared scheduled job

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
