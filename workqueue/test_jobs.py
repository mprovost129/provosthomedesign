from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings


class QueueJobRunnerTests(TransactionTestCase):
    @override_settings(WORK_INTAKE_ENABLED=False, BOOKING_ENABLED=True)
    def test_disabled_features_never_dispatch(self):
        with patch("workqueue.management.commands.run_queue_jobs.call_command") as dispatch:
            call_command("run_queue_jobs", stdout=StringIO())
        dispatch.assert_not_called()

    @override_settings(WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
    def test_intake_dispatches_email_and_cleanup_only(self):
        with patch("workqueue.management.commands.run_queue_jobs.call_command") as dispatch:
            call_command("run_queue_jobs", email_limit=17, stdout=StringIO())
        self.assertEqual([c.args[0] for c in dispatch.call_args_list],
                         ["process_queue_emails", "cleanup_queue_uploads"])
        self.assertEqual(dispatch.call_args_list[0].kwargs["limit"], 17)

    @override_settings(WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=True)
    def test_calendar_failure_does_not_prevent_receipts_or_cleanup(self):
        errors = StringIO()
        with patch("workqueue.management.commands.run_queue_jobs.call_command",
                   side_effect=[RuntimeError("private-provider-response"), None, None]) as dispatch:
            with self.assertRaisesMessage(CommandError, "sync_queue_appointments"):
                call_command("run_queue_jobs", appointment_limit=12, stderr=errors, stdout=StringIO())
        self.assertEqual([c.args[0] for c in dispatch.call_args_list],
                         ["sync_queue_appointments", "process_queue_emails", "cleanup_queue_uploads"])
        self.assertEqual(dispatch.call_args_list[0].kwargs["limit"], 12)
        self.assertNotIn("private-provider-response", errors.getvalue())

    def test_invalid_limits_do_not_dispatch_any_task(self):
        with patch("workqueue.management.commands.run_queue_jobs.call_command") as dispatch:
            for options in ({"appointment_limit": 0}, {"email_limit": 1001}):
                with self.assertRaises(CommandError):
                    call_command("run_queue_jobs", stdout=StringIO(), **options)
        dispatch.assert_not_called()
