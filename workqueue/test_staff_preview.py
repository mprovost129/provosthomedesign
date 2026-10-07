from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Attachment, WorkItem
from .services import create_work


@override_settings(WORK_QUEUE_ENABLED=False, WORK_QUEUE_STAFF_PREVIEW=True,
                   WORK_INTAKE_ENABLED=False, BOOKING_ENABLED=False)
class StaffPreviewTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("reviewer", is_staff=True)
        self.staff.user_permissions.add(Permission.objects.get(codename="view_workitem"))
        self.item = create_work(data={"contact_full_name": "Example Client", "company": "Homeowner",
            "contact_email": "client@example.invalid", "contact_phone": "5550100", "description": "Sample work"}, actor=None)[0]
        self.url = reverse("workqueue:queue")

    def test_preview_is_private_and_existing_queue_permission_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        user = get_user_model().objects.create_user("ordinary", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertContains(response, "Preview only")
        self.assertContains(response, self.item.reference)
        self.assertNotContains(response, "Save request")
        self.assertIn("no-store", response["Cache-Control"])

    def test_even_superuser_cannot_change_work_or_access_in_preview(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.invalid", "test-only"))
        response = self.client.get(self.url)
        self.assertFalse(response.context["can_add"])
        self.assertFalse(response.context["can_change"])
        self.assertFalse(response.context["can_manage_access"])
        response = self.client.post(self.url, {"action": "status", "item_id": self.item.pk,
            "version": 1, "status": "completed"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(WorkItem.objects.get().status, "new")

    def test_original_files_are_staff_only_and_client_routes_stay_disabled(self):
        attachment = Attachment.objects.create(submission=self.item.submissions.get(),
            original_name="Archived plan", legacy_drive_id="drive_file_test_1234")
        url = reverse("workqueue:download", args=[attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(url), "https://drive.google.com/file/d/drive_file_test_1234/view",
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("workqueue:submit")).status_code, 404)
        self.assertEqual(self.client.get(reverse("workqueue:book")).status_code, 404)
