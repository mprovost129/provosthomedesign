from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=True)
class WebsiteClientLinksTests(TestCase):
    def test_enabled_client_tools_are_visible_without_exposing_staff_queue(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/submit-work/"')
        self.assertContains(response, 'href="/track-work/"')
        self.assertContains(response, 'href="/book-appointment/"')
        self.assertContains(response, "Submit Your Project")
        self.assertNotContains(response, 'href="/work-queue/"')

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_disabled_intake_does_not_advertise_unavailable_tools(self):
        response = self.client.get("/")
        for path in ["submit-work", "track-work", "book-appointment", "work-queue"]:
            self.assertNotContains(response, f'href="/{path}/"')
        self.assertContains(response, 'href="/get-started/"')

    @override_settings(BOOKING_ENABLED=False)
    def test_intake_can_be_linked_without_advertising_disabled_booking(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/submit-work/"')
        self.assertNotContains(response, 'href="/book-appointment/"')

    def test_queue_link_is_only_for_authorized_staff(self):
        user = get_user_model().objects.create_user(username="staff-link-test", is_staff=True)
        self.client.force_login(user)
        self.assertNotContains(self.client.get("/"), 'href="/work-queue/"')
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
        self.assertContains(self.client.get("/"), 'href="/work-queue/"')

    def test_web_services_subdomain_keeps_its_own_navigation(self):
        response = self.client.get("/", HTTP_HOST="web.provosthomedesign.com")
        self.assertEqual(response.status_code, 200)
        for path in ["submit-work", "track-work", "book-appointment", "work-queue"]:
            self.assertNotContains(response, f'href="/{path}/"')
