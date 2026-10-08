import uuid
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token
from .information_requests import request_information
from .information_responses import connect_information_responses, review_information_response
from .models import InformationResponse, NotificationDelivery, ProjectAccess, QueueState, Status, WorkItem
from .services import create_work, edit_work
from .test_client_intake import new_answers, valid_pdf
from .tests import work_data


@override_settings(DEBUG=True, WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True,
    INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com", INTAKE_LOCAL_DEVELOPMENT=True,
    RECAPTCHA_ENTERPRISE_API_KEY="", RECAPTCHA_PRIVATE_KEY="", RECAPTCHA_SECRET_KEY="")
class InformationResponseTests(TestCase):
    def setUp(self):
        cache.clear()
        files = TemporaryDirectory()
        self.addCleanup(files.cleanup)
        storage = override_settings(STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "intake_private": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": files.name}}})
        storage.enable()
        self.addCleanup(storage.disable)
        self.owner = get_user_model().objects.create_superuser("reply-owner", "owner@example.invalid", "test-only")
        self.staff = Client()
        self.staff.force_login(self.owner)
        self.url = reverse("workqueue:queue")
        token = self.client.get(reverse("workqueue:submit")).context["form"].initial["intake_token"]
        self.assertEqual(self.client.post(reverse("workqueue:submit"), new_answers(intake_token=token)).status_code, 302)
        self.original = WorkItem.objects.get()

    def ask(self, item=None):
        item = item or self.original
        item.refresh_from_db()
        request_information(item_id=item.pk, version=item.version, message="Please send your survey.",
            followup_date=None, token=uuid.uuid4(), actor=self.owner)
        return item.information_requests.latest("pk")

    def verify(self, email="alex@example.invalid"):
        _, raw = mint_email_token(email)
        self.assertEqual(self.client.post(reverse("workqueue:confirm_access"), {"token": raw}).status_code, 302)

    def update(self, *, reference=None, context="", email="alex@example.invalid", file=False):
        page = self.client.get(reverse("workqueue:submit"), {"kind": "update", "reference": reference or ""})
        token = page.context["form"].initial["intake_token"]
        data = {"terms_accepted": "on", "kind": "update", "intake_token": token,
            "contact_full_name": "Alex Example", "company": "Homeowner", "contact_email": email,
            "contact_phone": "508-555-0100", "project_reference": reference or "", "project_context": context,
            "update_description": "Here is the survey you requested.", "categories": ["Answers or corrections"]}
        if file:
            uploaded = self.client.post(reverse("workqueue:start_upload"), {"intake_token": token, "file": valid_pdf("survey.pdf")})
            self.assertEqual(uploaded.status_code, 200)
            data["upload_ids"] = uploaded.json()["id"]
        response = self.client.post(reverse("workqueue:submit"), data)
        self.assertEqual(response.status_code, 302)
        return WorkItem.objects.latest("received_at")

    def review_payload(self, response):
        self.original.refresh_from_db()
        return {"action": "review_information_response", "item_id": self.original.pk,
                "response_id": response.pk, "version": self.original.version, "filter_attention": "responses", "filter_scope": "all"}

    def test_anonymous_return_waits_for_verification_then_shows_files_without_reordering(self):
        question = self.ask()
        update = self.update(reference=self.original.reference, file=True)
        self.assertFalse(InformationResponse.objects.exists())
        self.verify()
        response = InformationResponse.objects.get()
        self.assertEqual(response.information_request, question)
        self.assertEqual(response.work_item, update)
        self.original.refresh_from_db()
        update.refresh_from_db()
        self.assertEqual(update.project_id, self.original.project_id)
        self.assertEqual((self.original.queue_order, update.submission_position), (1, 2))
        self.assertEqual(self.original.status, Status.NEEDS_INFORMATION)
        self.assertEqual(NotificationDelivery.objects.count(), 5)
        queue = self.staff.get(self.url, {"attention": "responses", "scope": "all"})
        self.assertEqual([row["item"].pk for row in queue.context["rows"]], [self.original.pk])
        self.assertContains(queue, "Response received—review needed (1)")
        self.assertContains(queue, "survey.pdf")
        self.assertContains(queue, "Here is the survey you requested.")
        counts = {row["key"]: row["count"] for row in queue.context["attention_summary"]}
        self.assertEqual(counts["responses"], 1)
        self.assertEqual(counts["information"], 0)

    def test_verified_submission_connects_immediately_and_review_is_audited(self):
        self.verify()
        self.ask()
        self.update(reference=self.original.reference)
        response = InformationResponse.objects.get()
        page = self.staff.post(self.url, self.review_payload(response), follow=True)
        self.assertContains(page, "Job status is unchanged")
        response.refresh_from_db()
        self.assertEqual(response.reviewed_by, self.owner)
        self.assertIsNotNone(response.reviewed_at)
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, Status.NEEDS_INFORMATION)
        self.assertTrue(self.original.audit_events.filter(action="information_response_reviewed").exists())
        self.assertEqual(page.context["filters"]["attention"], "responses")
        self.assertEqual(len(page.context["rows"]), 0)
        history = self.staff.get(self.url)
        self.assertContains(history, "Reviewed")
        self.assertNotContains(history, "Response received—review needed")

    def test_different_verified_email_and_revoked_access_cannot_mark_a_response(self):
        self.ask()
        self.update(reference=self.original.reference, email="other@example.invalid")
        self.verify("other@example.invalid")
        self.assertFalse(InformationResponse.objects.exists())
        self.verify()
        ProjectAccess.objects.filter(project=self.original.project).update(revoked_at=timezone.now())
        self.update(reference=self.original.reference)
        self.verify()
        self.assertFalse(InformationResponse.objects.exists())

    def test_update_submitted_before_question_is_not_a_response(self):
        self.update(reference=self.original.reference)
        self.ask()
        self.verify()
        self.assertFalse(InformationResponse.objects.exists())

    def test_project_name_connects_only_one_waiting_request(self):
        self.verify()
        self.ask()
        self.update(context=self.original.project.name)
        self.assertEqual(InformationResponse.objects.count(), 1)
        second = create_work(data=work_data(project=self.original.project, contact_email=self.original.contact_email), actor=self.owner)[0]
        self.ask(second)
        self.update(context=self.original.project.name)
        self.assertEqual(InformationResponse.objects.count(), 1)

    def test_latest_question_receives_response_and_repeated_verification_is_idempotent(self):
        self.verify()
        first = self.ask()
        latest = self.ask()
        self.update(reference=self.original.reference)
        self.verify()
        self.assertEqual(InformationResponse.objects.count(), 1)
        self.assertEqual(InformationResponse.objects.get().information_request, latest)
        self.assertFalse(first.responses.exists())
        self.assertEqual(self.original.audit_events.filter(action="information_response_received").count(), 1)

    def test_multiple_replies_are_reviewed_individually_and_later_revision_does_not_reopen(self):
        self.verify()
        self.ask()
        self.update(reference=self.original.reference)
        self.update(reference=self.original.reference)
        responses = list(InformationResponse.objects.order_by("pk"))
        self.assertEqual(len(responses), 2)
        self.staff.post(self.url, self.review_payload(responses[0]))
        self.assertContains(self.staff.get(self.url), "Response received—review needed (1)")
        self.staff.post(self.url, self.review_payload(responses[1]))
        self.update(reference=self.original.reference)
        self.assertEqual(InformationResponse.objects.count(), 2)
        question = self.ask()
        self.update(reference=self.original.reference)
        self.assertEqual(InformationResponse.objects.count(), 3)
        self.assertEqual(question.responses.count(), 1)

    def test_review_checks_permission_csrf_ownership_and_stale_version(self):
        self.verify()
        self.ask()
        self.update(reference=self.original.reference)
        response = InformationResponse.objects.get()
        payload = self.review_payload(response)
        reader = get_user_model().objects.create_user("reply-reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        client = Client()
        client.force_login(reader)
        self.assertEqual(client.post(self.url, payload).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, payload).status_code, 403)
        other = create_work(data=work_data(), actor=self.owner)[0]
        self.assertEqual(self.staff.post(self.url, {**payload, "item_id": other.pk}).status_code, 400)
        self.original.refresh_from_db()
        edit_work(item_id=self.original.pk, version=self.original.version, data={"status": Status.IN_PROGRESS}, actor=self.owner)
        self.assertEqual(self.staff.post(self.url, payload).status_code, 409)
        response.refresh_from_db()
        self.assertIsNone(response.reviewed_at)
        self.assertEqual(self.staff.post(self.url, self.review_payload(response)).status_code, 302)
        self.assertEqual(self.staff.post(self.url, payload).status_code, 302)

    def test_closed_original_stays_closed_and_remains_visible_for_response_review(self):
        self.verify()
        self.ask()
        self.original.refresh_from_db()
        edit_work(item_id=self.original.pk, version=self.original.version, data={"status": Status.COMPLETED}, actor=self.owner)
        self.update(reference=self.original.reference)
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, Status.COMPLETED)
        queue = self.staff.get(self.url, {"attention": "responses", "scope": "all"})
        self.assertContains(queue, "Response received—review needed")


@skipUnlessDBFeature("has_select_for_update")
@override_settings(INTAKE_PUBLIC_BASE_URL="https://www.provosthomedesign.com")
class InformationResponseConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_user("reply-concurrency", is_staff=True)
        self.original = create_work(data=work_data(), actor=self.owner)[0]
        ProjectAccess.objects.create(project=self.original.project, email=self.original.contact_email)
        request_information(item_id=self.original.pk, version=1, message="Survey please", followup_date=None,
                            token=uuid.uuid4(), actor=self.owner)
        update = create_work(data=work_data(kind="update", project=self.original.project,
            previous_request=self.original), actor=self.owner)[0]
        update.submissions.update(channel="website")

    def test_parallel_verifications_create_only_one_flag_and_parallel_reviews_are_idempotent(self):
        def connect(_):
            close_old_connections()
            try:
                return connect_information_responses(self.original.contact_email)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(connect, range(2))), [0, 1])
        response = InformationResponse.objects.get()
        self.original.refresh_from_db()
        version = self.original.version
        def review(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.owner.pk)
                return review_information_response(item_id=self.original.pk, response_id=response.pk,
                    version=version, actor=actor)[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(review, range(2))), [True, False])
