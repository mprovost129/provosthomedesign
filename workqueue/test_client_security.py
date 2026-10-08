from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .booking_views import AppointmentForm
from .client_forms import ClientIntakeForm
from .models import (Appointment, BookingAccess, BookingAudit, EmailAccessToken,
                     NotificationDelivery, PendingUpload, Submission, WorkItem)
from .test_booking import NOW, START, CalendarDouble
from .test_client_intake import new_answers, valid_pdf


@override_settings(WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True, BOOKING_ENABLED=True,
    INTAKE_LOCAL_DEVELOPMENT=False, INTAKE_DIRECT_UPLOADS=False, BOOKING_CALENDAR_BACKEND="google",
    GOOGLE_CALENDAR_ID="primary", RECAPTCHA_SITE_KEY="test-public-key",
    RECAPTCHA_ENTERPRISE_API_KEY="test-server-key", RECAPTCHA_ENTERPRISE_PROJECT_ID="test-project",
    RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY="", RECAPTCHA_MIN_SCORE=0.5)
class ClientSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.files = TemporaryDirectory()
        storage = override_settings(STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "intake_private": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                               "OPTIONS": {"location": self.files.name, "base_url": None}}})
        storage.enable()
        self.addCleanup(storage.disable)
        self.addCleanup(self.files.cleanup)
        self.token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]

    def assessment(self, *, valid=True, score=0.9, action="work_submission", host="testserver"):
        response = Mock()
        response.json.return_value = {"tokenProperties": {"valid": valid, "action": action, "hostname": host},
                                      "riskAnalysis": {"score": score}}
        return patch("requests.post", return_value=response)

    def submit(self, **changes):
        return self.client.post(reverse("workqueue:submit"), new_answers(**{
            "intake_token": self.token, "recaptcha_token": "test-single-use-token", **changes}))

    def test_terms_required_for_new_updates_and_appointments(self):
        for kind in ("new", "update"):
            data = new_answers(kind=kind, intake_token=self.token, terms_accepted="",
                project_context="Kitchen addition", update_description="New plans", categories=["Plans or survey"])
            form = ClientIntakeForm(data)
            self.assertFalse(form.is_valid())
            self.assertIn("terms_accepted", form.errors)
            with patch("requests.post") as api:
                response = self.client.post(reverse("workqueue:submit"), data)
            self.assertEqual(response.status_code, 400)
            api.assert_not_called()
        form = AppointmentForm({"day": START.date(), "slot": START.isoformat(), "token": "test",
            "full_name": "Alex", "phone": "555", "purpose": "Review"}, slots=[START])
        self.assertFalse(form.is_valid())
        self.assertIn("terms_accepted", form.errors)
        self.assertFalse(WorkItem.objects.exists())

    def test_production_missing_token_or_configuration_never_allows_submission(self):
        with patch("requests.post") as api:
            self.assertEqual(self.submit(recaptcha_token="").status_code, 400)
            with override_settings(RECAPTCHA_ENTERPRISE_API_KEY="", RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY=""):
                self.assertEqual(self.submit().status_code, 400)
                with override_settings(INTAKE_LOCAL_DEVELOPMENT=True, DEBUG=False):
                    self.assertEqual(self.submit().status_code, 400)
        api.assert_not_called()
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_invalid_low_score_wrong_action_and_foreign_hostname_all_reject(self):
        for change in ({"valid": False}, {"score": 0.1}, {"action": "contact_form"}, {"host": "attacker.invalid"}):
            with self.subTest(change=change), self.assessment(**change):
                self.assertEqual(self.submit().status_code, 400)
        with patch("requests.post", side_effect=TimeoutError):
            self.assertEqual(self.submit().status_code, 400)
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_valid_submission_records_consent_and_uses_fixed_server_action(self):
        with self.assessment() as api:
            response = self.submit()
        self.assertEqual(response.status_code, 302)
        event = api.call_args.kwargs["json"]["event"]
        self.assertEqual(event["expectedAction"], "work_submission")
        answers = Submission.objects.get().answers
        self.assertEqual(answers["Terms accepted"], "True")
        self.assertEqual(answers["Terms URL"], reverse("pages:terms"))
        self.assertTrue(answers["Terms accepted at"])
        self.assertNotIn("test-single-use-token", str(answers))
        self.assertEqual(NotificationDelivery.objects.count(), 2)

    def test_unverified_upload_cannot_reserve_storage_or_receive_s3_policy(self):
        data = {"intake_token": self.token, "name": "plans.pdf", "size": 300}
        with override_settings(INTAKE_DIRECT_UPLOADS=True), patch("workqueue.client_views.direct_upload_policy") as policy:
            self.assertEqual(self.client.post(reverse("workqueue:start_upload"), data).status_code, 400)
            with self.assessment(valid=False, action="work_upload"):
                self.assertEqual(self.client.post(reverse("workqueue:start_upload"),
                    {**data, "recaptcha_token": "bad"}).status_code, 400)
        policy.assert_not_called()
        self.assertFalse(PendingUpload.objects.exists())

    def test_upload_and_submit_use_separate_actions_and_failed_submit_retains_file(self):
        with self.assessment(action="work_upload") as api:
            response = self.client.post(reverse("workqueue:start_upload"),
                {"intake_token": self.token, "file": valid_pdf(), "recaptcha_token": "upload-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.call_args.kwargs["json"]["event"]["expectedAction"], "work_upload")
        upload = PendingUpload.objects.get()
        with self.assessment(valid=False):
            failed = self.submit(upload_ids=str(upload.pk), categories=["Plans or survey"])
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(list(failed.context["ready_uploads"]), [upload])
        self.assertEqual(failed.context["form"].data["contact_full_name"], "Alex Example")
        upload.refresh_from_db()
        self.assertEqual(upload.state, "ready")
        with self.assessment():
            self.assertEqual(self.submit(upload_ids=str(upload.pk), categories=["Plans or survey"]).status_code, 302)

    def test_signin_requests_require_captcha_before_tokens_or_emails(self):
        for route, action in (("tracking", "work_tracking_signin"), ("book", "work_booking_signin")):
            for email in ("alex@example.invalid", "unknown@example.invalid"):
                with self.assessment(valid=False, action=action):
                    response = self.client.post(reverse("workqueue:" + route),
                        {"email": email, "recaptcha_token": "bad"})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(EmailAccessToken.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_valid_signins_preserve_uniform_response_and_bind_actions(self):
        BookingAccess.objects.create(email="alex@example.invalid")
        for route, action in (("tracking", "work_tracking_signin"), ("book", "work_booking_signin")):
            for email in ("alex@example.invalid", "unknown@example.invalid"):
                with self.assessment(action=action) as api:
                    response = self.client.post(reverse("workqueue:" + route),
                        {"email": email, "recaptcha_token": "test-token"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["sent"])
                self.assertEqual(api.call_args.kwargs["json"]["event"]["expectedAction"], action)

    def test_booking_requires_captcha_and_records_terms_without_duplicate_reservations(self):
        BookingAccess.objects.create(email="alex@example.invalid")
        session = self.client.session
        session["queue_verified_email"] = "alex@example.invalid"
        session["queue_email_verified_until"] = (NOW + timedelta(days=7)).timestamp()
        session.save()
        calendar = CalendarDouble()
        with patch("django.utils.timezone.now", return_value=NOW), \
             patch("workqueue.booking_views.available_times", return_value=[START]), \
             patch("workqueue.booking.calendar_provider", return_value=calendar):
            token = self.client.get(reverse("workqueue:book"), {"day": START.date().isoformat()}).context["form"].initial["token"]
            data = {"token": token, "day": START.date().isoformat(), "slot": START.isoformat(),
                "full_name": "Alex", "phone": "555", "purpose": "Review", "terms_accepted": "on"}
            self.assertEqual(self.client.post(reverse("workqueue:book"), data).status_code, 400)
            self.assertFalse(Appointment.objects.exists())
            with self.assessment(action="work_booking") as api:
                self.assertEqual(self.client.post(reverse("workqueue:book"), {**data, "recaptcha_token": "test-token"}).status_code, 302)
            self.assertEqual(api.call_args.kwargs["json"]["event"]["expectedAction"], "work_booking")
            # A replay returns the existing reservation rather than assessing a used token again.
            with patch("requests.post") as api:
                self.assertEqual(self.client.post(reverse("workqueue:book"), data).status_code, 302)
                api.assert_not_called()
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(BookingAudit.objects.get(action="booking_requested").details["terms_url"], reverse("pages:terms"))

    def test_form_displays_unchecked_required_terms_and_existing_terms_link(self):
        response = self.client.get(reverse("workqueue:submit"))
        self.assertContains(response, 'href="/terms/"')
        self.assertContains(response, 'name="terms_accepted" required')
        self.assertFalse(response.context["form"]["terms_accepted"].value())
        self.assertContains(response, "recaptcha/enterprise.js?render=test-public-key")
