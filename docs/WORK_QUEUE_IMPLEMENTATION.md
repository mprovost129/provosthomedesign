# Work queue foundation — October 7, 2026

This stage adds a private work-queue page and database schema to the existing Django project. It does not replace the working Google intake yet, import real records, send receipt emails, or change Render.

## Available

- `/work-queue/`: one staff page with active/all/closed views, search, status, priority and received-date filters, 25 requests per page and alternating colors.
- Inline status changes and expandable controls for order, project linking, estimates, dates, follow-up and internal notes.
- Manual capture for phone/email work; retrying the same submitted form does not allocate a second request.
- Contact, company, separate billing/project address snapshots, complete captured answers and project work history.
- Each update gets a separate reference and position, connected to a shared project. Ambiguous updates can be queued without a link and connected on the same page.
- Completed/Canceled/Declined/Duplicate leave Active Work automatically. On Hold/Needs Information/In Progress remain active, matching the existing Sheet rules.
- Positions count the full active queue before filtering or pagination. Priority does not silently change order; receipt snapshots are separate from current positions.
- Audited writes, conflicting-edit detection, CSRF protection, staff and explicit queue permissions, and no caching or public analytics on the queue page.
- PostgreSQL singleton row locking for reference/order allocation and writes. Migration seeds that row before first use.
- Schema for authorized project access, submission/attachment metadata and a future notification outbox. No public download/receipt/booking behavior is claimed by this stage.

## Safe local development

Use `config.settings_queue_dev`. This standalone settings module never loads production settings or `.env`, ignores Render's `DATABASE_URL` and older `DB_*` configuration, stores development data in `queue-preview.sqlite3`, and uses an in-memory email backend. It includes the existing site applications and URL configuration for regression checking.

With the project's dependencies installed in a working Python environment:

```text
python manage.py migrate --settings=config.settings_queue_dev
python manage.py createsuperuser --settings=config.settings_queue_dev
python manage.py runserver --settings=config.settings_queue_dev
```

Open `/work-queue/` and sign in through the Django admin login. Use fictitious projects in this preview. Never run these commands without the explicit settings selection for queue development. The existing `settings_dev.py` is not the queue isolation configuration.

Superusers can manage the queue. Other staff require `workqueue.view_workitem`; grant `workqueue.add_workitem` for capture and `workqueue.change_workitem` for editing. Read-only staff are not shown write controls. No direct editable queue ModelAdmin is registered, so the management page remains the write path.

## Validation

```text
python manage.py test pages plans workqueue --settings=config.settings_queue_dev
python manage.py makemigrations --check --dry-run --settings=config.settings_queue_dev
python manage.py check --settings=config.settings_queue_dev
```

SQLite checks verify behavior but do not prove PostgreSQL row locking. Two `TransactionTestCase` concurrency checks exercise parallel requests and parallel retries when run against PostgreSQL. `config.settings_queue_postgres_test` accepts only an explicit `QUEUE_TEST_DATABASE_URL` pointing to localhost and a database whose name starts `phd_queue_test`; it rejects Render/remote hosts and does not read `.env`. Use a disposable local PostgreSQL database/user with test-database creation permission, then:

```text
python manage.py test workqueue --settings=config.settings_queue_postgres_test
```

Local PostgreSQL validation is still required before production enablement if those checks are skipped. Docker Desktop was installed but its local database engine did not become available during this implementation session. No tests were run against Render.

Final validation: 103 tests discovered across pages, plans and workqueue; 101 passed and the two PostgreSQL-only concurrency checks were skipped on SQLite. Django system checks and migration consistency checks passed. The staff page was also reviewed in the in-app browser using fictitious local records.

The existing Windows virtual-environment launcher could not start. Verification used the bundled Python runtime and the project's installed pure-Python packages; production requirements were not changed. The local `.env` does not supply `DATABASE_URL`; Render's deployed environment and live record count have not been inspected.

## Before production enablement

`WORK_QUEUE_ENABLED` defaults to `False` in production settings. Keep it off during schema deployment and historical import. Schema migrations only create the new queue tables and its empty allocator; they do not alter existing inquiry records.

Before enabling, preserve/import the real queue and issued-reference high-water mark into `QueueState.last_reference_number`, import existing order values and reconcile each record and file. The allocator's starting value of zero is for isolated new data only; enabling an empty production queue before importing could reuse historical PHD references. Import code must also preserve incomplete historical contact/address data without fabricating it. No existing inquiry is automatically promoted into the new queue.

Stage 2 now implements unified public New/Update intake, private uploads and authorized downloads, client/owner email outbox delivery, and passwordless client tracking and project selection. See [Client intake implementation](CLIENT_INTAKE_IMPLEMENTATION.md) for the current behavior, validation and production setup. Checked Sheet import/cutover and controlled Google Calendar booking remain upcoming. The site's public S3 media configuration must not be reused for client documents. Google intake remains authoritative until a verified cutover.

No PowerShell deployment helper is included, following the user's updated instruction.

Queue locking follows [Django's `select_for_update` documentation](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update); isolated validation follows [Django's testing documentation](https://docs.djangoproject.com/en/5.2/topics/testing/overview/).
