# Queue recovery and monitoring

## Database recovery check — October 8, 2026

Render's existing production database showed seven days of point-in-time recovery and logical exports retained for at least seven days. A newly generated logical export was downloaded and restored to an isolated, disposable local database. No production database was overwritten, and no new Render database or paid service was created.

The export was from PostgreSQL 18.6 and restored successfully with PostgreSQL 17. The check verified 28 work items, 32 submission records and 19 attachment records, with zero orphaned submission/attachment relationships. The disposable restored database was stopped and removed after verification. The original export was retained locally. This verifies that this logical export can be read/restored; it is not a test of Render's point-in-time restore process or of every application's behavior after restoration.

In a recovery incident, preserve the affected database, restore to a separate database first, compare request IDs/counts/relationships, run migrations for the deployed code, and verify access and private file downloads before redirecting the application. Prevent restored pending/unknown notifications and calendar operations from replaying until their real delivery outcomes are reviewed. Do not use a restored database to send test messages to clients.

## Private file storage — October 8, 2026

Bucket `phd-intake-private-prod` in `us-east-1` was inspected: Block Public Access is on, ownership is bucket-owner enforced with ACLs disabled, and encryption uses S3-managed keys. At inspection time, bucket versioning was disabled and there were no lifecycle or replication rules. Therefore accidental deletion/overwrite recovery has not yet been established for stored documents. Database exports contain file references, not S3 file contents.

Enabling versioning has been proposed for owner approval. Normal S3 storage charges apply to retained versions. No permanent version deletion, lifecycle expiry or broader app permissions should be introduced without explicit approval. Versioning alone is not an independent backup against loss of the AWS account. Once enabled, verify recovery using disposable samples without deleting client files, and record the result here.

## Independent monitor activation

Saved Google Apps Script project:
https://script.google.com/u/0/home/projects/1VFqKnIJUQ_svkwOC3SSFvcjuuNiLFxgr4jqMjyCQ0_Ou3Ez1qKUrgxi-/edit

`ops/WebsiteQueueMonitor.gs` is added alongside the existing intake scripts. Existing OAuth scopes are preserved; `https://www.googleapis.com/auth/script.external_request` is added for reading the public website health endpoint. No API keys, private Render settings, client names or files are copied into the monitor.

After the website migrations and worker are healthy, select `enableWebsiteQueueMonitor`, run it once, and complete Google's permission prompt. Check that exactly one `checkWebsiteQueueHealth` trigger runs every five minutes. Then run `checkWebsiteQueueHealth` once and confirm success in Executions. Do not enable the monitor before the website exposes the endpoint.

Checks use `https://www.provosthomedesign.com/queue-health/` and require HTTP 200 with `ok: true`. Two failed checks produce one alert to `mike@provosthomedesign.com`; a later healthy check produces one recovery message. Repeated failure in the same incident does not email repeatedly. If sending the alert has an uncertain outcome, the monitor intentionally does not retry automatically; review Google's execution history. Apps Script execution/quota failures are a separate dependency and its account should remain monitored.

Local monitor tests verify trigger deduplication, failure threshold, one alert/recovery per incident and uncertain-email handling. The existing Render worker remains responsible for actual queue operations. The monitor does not create clients/jobs, send client messages, modify calendars or expose private records.

## Deployment and monitoring verified — October 8, 2026

Render deployed application commit `485d287a46f5dfee2cc1a2f8f065bdc457f7df7d` successfully to the web service (`dep-db3re1g473hc73bu44ag`) and the existing cron worker (`bld-db3re1o473hc73bu4570`). Repeated subsequent worker runs succeeded. The live queue showed current background jobs and zero email issues. Public submission drafts and staff completed-file delivery links were present. No client jobs were changed or test messages sent to clients during these live checks.

The public health endpoint returned HTTP 200 with `{"ok": true}` and no-store caching. The Google Apps Script project has exactly one `checkWebsiteQueueHealth` time-based trigger. Its setup uses a five-minute interval. A focused Google-hosted `verifyWebsiteQueueMonitor` run returned `httpStatus: 200` and `healthy: true`, and the scheduled handler was also run successfully. The first health check had reported a failure; the later diagnostic confirmed connectivity. Do not infer successful connectivity merely from a completed execution, because the normal handler deliberately catches connection failures. Its failure counter must also clear on a healthy check.

S3 versioning approval remains pending. The original disabled-versioning finding above still applies; document recovery is not claimed as verified.
