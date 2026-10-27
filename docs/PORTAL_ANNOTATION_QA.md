# Portal annotation editing validation

Validated locally on 2026-10-27, based on `f0560b3` (the available main snapshot). No remote branch publication, deployment, real client submission, mail delivery, or live queue writes were performed.

## Automated checks

- `node --check static/js/plan-review.js` and `node --check static/js/plan-review-model.mjs`: passed.
- `node --test workqueue/js_tests/*.test.cjs ops/test_website_monitor.cjs`: 45 passed, including 11 new annotation tests.
- `python manage.py check --settings=config.settings_queue_dev`: passed.
- `python manage.py makemigrations --check --dry-run --settings=config.settings_queue_dev`: no changes.
- `python manage.py test --settings=config.settings_queue_dev --noinput`: 1,449 tests, OK, 57 skipped. The additional drawing round-trip test was added while this aggregate run was in progress and was subsequently run in the focused suite below.
- `python manage.py test workqueue.test_portal_reviews workqueue.test_portal_drawing workqueue.test_portal workqueue.test_portal_discussions --settings=config.settings_queue_dev --noinput`: 124 tests, OK, 2 skipped (PostgreSQL row-lock tests). This includes the additional adjusted-mark round-trip/export test.
- Production-style `collectstatic`, using an isolated output directory and `CompressedManifestStaticFilesStorage`: passed; both the editor and helper module receive hashed URLs. The template resolves the helper URL through Django static storage.
- `git diff --check`: passed.

On Windows, set `PYTHONUTF8=1` for these test commands; an existing seed migration prints Unicode status symbols. The isolated settings never load production credentials and use local test storage and in-memory email.

The new tests exercise add/remove/move/resize, whole-stroke translation and edge clamping, invalid collapsed shapes, undo/redo and redo invalidation, deliberate comment/general-note edits, native text shortcut exclusions, serialized saves and version acknowledgement, failed saves and conflicts, page changes during gestures, pointer cancellation/lost capture, secondary pointer exclusion, simulated touch pointers, readonly reviews, reload/session history, and submission locking. The new Django test saves adjusted legacy pins and each existing shape type, freezes their comments/coordinates, creates a marked export, and verifies the source PDF and submitted annotations remain unchanged.

## Browser verification

Used the supported Chrome browser workflow with the actual rendered portal template, inherited CSS, current JavaScript and self-hosted PDF.js. The plan was a generated two-page synthetic PDF; a loopback fixture simulated draft save responses, version conflicts, and failures. Server persistence, authorization, queue/outbox and export behavior were exercised separately by Django tests.

Verified through actual mouse/keyboard interaction:

- Repeated note/shape placement remains in the selected drawing mode and keeps focus on the plan.
- Direct pin/shape selection, pin movement, box outline movement and resizing, arrow endpoint dragging, and whole freehand movement.
- Visible undo/redo, keyboard undo/redo, keyboard mark selection/movement and endpoint adjustment, comment undo/redo, and native text-field Ctrl+Z.
- Save/retry after a simulated HTTP failure; conflict warning persists and further local edits do not generate another write.
- Saved coordinates and comments survive reload; history starts fresh.
- Page changes, rapid zoom changes, and fit-page resizing; no browser error/warning logs during the final checks.
- Phone (390x844) and tablet (768x1024) viewport overrides: no document-width overflow; tool targets at least 44 CSS pixels high. Restored the normal browser viewport afterward.

## Limits

- This is Chrome desktop validation plus responsive viewport simulation. No physical touchscreen/stylus, Safari, Firefox, or screen-reader device test was performed. Touch pointer cancellation/movement is covered by the event harness, not a physical-device claim.
- PostgreSQL-specific concurrency tests remain skipped under SQLite; run the existing PostgreSQL CI job before publication/deployment.
- Undo/redo belongs to the current open tab. Comment edits enter markup history on field exit; native text undo remains available while typing. Saved history is not retained across reloads.
- Boxes expose two opposite-corner handles; arrows expose two endpoint handles. Freehand moves as a whole stroke. Existing limits remain 100 marks, 300 points per stroke, 3,000 total points, and 500 pages.
- No new tools, pan/pinch redesign, integration, migration, annotation schema, persistence service, export implementation, or production configuration was introduced.
