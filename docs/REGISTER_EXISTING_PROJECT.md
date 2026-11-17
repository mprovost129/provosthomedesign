# Register an existing project

Staff can use **Register existing project** from the Work queue or Projects page,
or open `/work-projects/register-existing/`. This is for a project that already
exists in Dropbox and FreshBooks but has no native PHD project. It does not need
a prior submission or a placeholder queue request.

1. Select the active CRM contact under the correct client/company. That client
   must already have an explicit FreshBooks billing-client connection for the
   selected business. Resolve a missing connection through the existing client
   Billing connection screen first; this flow never creates or guesses a client.
2. Select the existing Dropbox folder. **Refresh existing Dropbox folders** reads
   the remote listing and updates only the local folder index.
3. Enter the actual FreshBooks project ID and the PHD project name/address.
   A mirror row UUID, invoice ID, accounting account ID and six-digit business
   project number are different identifiers and cannot substitute for this ID.
4. Choose **Verify and review**. Review the CRM contact, billing client, business
   and accounting account, Dropbox account/folder, project number and address.
5. Choose **Register existing project**. The project workspace opens with a
   confirmation that no work was added. **Add update to this project** opens the
   normal queue form with current CRM and project details. Enter the real service
   and requested changes, then separately choose **Add to queue**.

The project is also available in the normal update selector and in Projects with
**Show: All projects**, even before its first request. It is not active queue work
until a real update is submitted. The first update has no previous request; later
updates use the normal latest/source-request behavior.

Project labels use the saved Dropbox business number when available. The workspace
shows the native FreshBooks connection independently of copied mirror records and
can display an exact matching source project before the next mirror scan. These
display reads do not rewrite project references or saved mirror associations.

## Boundaries and checks

The flow requires active staff and all four existing permissions:
`view_workitem`, `add_workitem`, `change_workitem`, `add_workproject`. It is disabled
when the queue is disabled or in staff preview mode. GET never registers anything;
POSTs use CSRF protection and responses are not cached. No permissions are granted
by this feature.

Verification uses current provider read APIs, not a possibly stale mirror:

- FreshBooks account/business membership, active billing client and active project,
  with the project's client ID matching the explicit CRM billing link.
- Dropbox account and root namespace, the original Projects root, and an existing
  immediate child folder.
- The same six-digit `YYMM##` number followed by a hyphen in both provider names,
  with no duplicate number in either remote listing. Unnumbered, mismatched,
  unavailable or ambiguous records fail closed for staff review.
- Local project-number, folder and FreshBooks-project collisions. Existing native
  projects are never silently adopted, merged, reassigned or overwritten.

The signed 15-minute review is bound to its staff member and exact inputs,
contact/billing identity, provider accounts and current provider names. Approval
revalidates those facts. Provider connection locks serialize concurrent approvals;
the existing queue lock protects the final CRM/collision check without advancing
either allocator. Unique constraints and one transaction prevent partial or
duplicate registrations. Identical repeat approvals return the same project;
conflicting reviews are rejected.

Provider reads run before the local write transaction so a failed registration
cannot roll back a rotated OAuth token. The transaction locks and rechecks both
connection identities and the CRM link before saving the project graph.
Dropbox pagination stops before requesting another page once its 20-second
listing budget is spent; an incomplete listing never replaces the local index.
Individual in-flight provider requests still use their existing network timeouts.

After a reversible CRM merge, FreshBooks reuse follows the surviving CRM client
only when its explicit account/customer billing link still matches. The original
project-link ownership and registration audit remain intact, including on undo.

Registration saves a `WorkProject`, `ProjectFolder`, already-linked
`FreshBooksProject`, `ProjectRegistration` provenance snapshot/contact/actor, and
`ProjectEvent`. It may refresh the local folder index and normal OAuth tokens.
It creates no `WorkItem`, `Submission`, invoice, outbox provisioning job, file copy,
Dropbox folder, provider project/client, notification, invitation, access token,
portal membership or access grant. It neither allocates a new business number nor
adds/reorders work. Provider adapters are only called for reads; the ordinary
OAuth token refresh mechanism remains available.

The FreshBooks project link may now have a null `work_item`, with a database check
requiring such a link to be `linked` with both remote project and customer IDs.
This prevents requestless registrations from entering the provisioning worker.
`ProjectRegistration.contact` supplies update defaults when no source request
exists. Existing submission, filing, receipt and portal workflows retain their
separate responsibilities.

## Validation and local preview

Use the repository's pinned requirements in an isolated environment. The safe
development settings never load `.env`, production database URLs or live email
configuration. On Windows, set `PYTHONUTF8=1` because existing seed migrations
print Unicode characters.

```sh
python manage.py check --settings=config.settings_queue_postgres_test
python manage.py makemigrations --check --dry-run --settings=config.settings_queue_postgres_test
python manage.py test workqueue.test_project_registration workqueue.test_queue_project_updates workqueue.test_freshbooks_projects workqueue.test_dropbox workqueue.test_pm --settings=config.settings_queue_postgres_test --noinput
python manage.py test --settings=config.settings_queue_postgres_test --noinput
node --test workqueue/js_tests/*.test.cjs ops/test_website_monitor.cjs
```

`QUEUE_TEST_DATABASE_URL` must identify a dedicated localhost PostgreSQL database
whose name starts `phd_queue_test`. SQLite is supported for a local browser preview,
but the registration race tests require PostgreSQL. All new fixtures are fictional;
provider API boundaries are mocked and unexpected provider writes fail tests.

Browser validation uses `config.settings_queue_dev`, a separate disposable local
database, fictional provider responses, blocked outbound HTTP, and the real staff
login/form/review/register/update pages. This checks layout and workflow without
touching production or contacting clients. It is not a live provider acceptance
test.

## Release gate and migration compatibility

This change is prepared locally on `codex/register-existing-project` from
`a208ce6c22dd48c8b64c9b3aefbec506c3886096`. Publication, merge, deployment and any
production registration require separate authorization. No production DB repair
is part of this change.

Before an authorized release:

1. Reconcile the branch with the then-current deployed main and rerun the checks
   above. Review the complete diff before public-repository publication.
2. Resolve migration ordering if the separate unshipped billing branch is also
   included. Both `0065_register_existing_project` and `0065_retainer_billing_flow`
   currently depend on `0064_design_delivery_forecasts`. They change different
   schema objects, but combining them creates two migration leaves. If neither
   has shipped, rebase/renumber the later change and update its dependency. If
   either migration is already recorded in a database, preserve that history and
   add an explicit merge migration depending on both leaves. Do not use `--fake`
   or reverse applied migrations to hide the conflict. Test the combined migration
   graph and combined update/billing behavior before release. The billing branch
   and its migration have not been incorporated or modified here.
3. Apply the approved migration through the normal release step
   (`python manage.py migrate --noinput`), before new web code serves traffic.
   Deploy the same approved commit to web and worker, run normal static collection
   and `python manage.py check`, and confirm the migration is applied. No new
   secrets, scopes beyond provider reads, cron jobs or hosting services are needed.
4. Verify staff access, the entry points and a read-only review against the intended
   existing accounts. Do not approve registration until the specific live project
   is separately authorized. Registering and adding its genuine update are separate
   production actions; neither is part of deployment or an automatic backfill.

Rollback can retain the additive registration table and nullable link field. Do not
reverse this migration after registrations exist: restoring the old NOT NULL
constraint would fail for requestless links and deleting provenance would lose
the audit. Older code also lacks first-update CRM defaults for registered projects;
restore the new code or review those defaults before proceeding with updates.
