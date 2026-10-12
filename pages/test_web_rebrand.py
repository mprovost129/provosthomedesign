"""Business-boundary and inquiry regressions for the Slater Woods rebrand."""
import json
import re
from time import time
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import WebDesignInquiry
from .views import WEB_CASE_STUDIES, WEB_SERVICE_PAGES


@override_settings(
    ALLOWED_HOSTS=["web.provosthomedesign.com", "www.provosthomedesign.com"],
    WEB_DESIGN_HOST="web.provosthomedesign.com",
    WEB_DESIGN_URL="https://web.provosthomedesign.com",
    RECAPTCHA_ENTERPRISE_API_KEY="", RECAPTCHA_SECRET_KEY="", RECAPTCHA_PRIVATE_KEY="",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class WebRebrandTests(TestCase):
    host = "web.provosthomedesign.com"

    def setUp(self):
        cache.clear()

    def test_all_public_pages_have_current_brand_canonical_and_valid_schema(self):
        paths = ["/", "/services/", "/work/", "/about/", "/pricing/", "/contact/", "/privacy/", "/terms/"]
        paths += [f"/work/{slug}/" for slug in WEB_CASE_STUDIES]
        paths += [f"/services/{slug}/" for slug in WEB_SERVICE_PAGES]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path + "?source=work", HTTP_HOST=self.host)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn("Slater Woods Design Company", html)
                self.assertIn(f'<link rel="canonical" href="https://{self.host}{path}"', html)
                self.assertNotIn("Provost Home Design Web Services", html)
                for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                    json.loads(block)
                for asset in re.findall(r'(?:src|href)="/static/([^"?]+)', html):
                    self.assertIsNotNone(finders.find(asset), asset)

    def test_four_projects_and_correct_ownership_on_home_and_work(self):
        for path in ["/", "/work/"]:
            response = self.client.get(path, HTTP_HOST=self.host)
            for study in WEB_CASE_STUDIES.values():
                self.assertContains(response, study["live_url"])
            self.assertContains(response, "Client project", count=2)
            self.assertContains(response, "Our own business", count=1)
            self.assertContains(response, "Our own publishing project", count=1)
        sitemap = self.client.get("/sitemap.xml", HTTP_HOST=self.host)
        for slug in WEB_CASE_STUDIES:
            self.assertContains(sitemap, f"https://{self.host}/work/{slug}/")

    def test_residential_brand_and_private_route_boundary(self):
        for path in ["/", "/about/", "/contact/", "/privacy/", "/terms/", "/get-started/"]:
            response = self.client.get(path, HTTP_HOST="www.provosthomedesign.com")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Provost Home Design")
            html = response.content.decode()
            footer = re.search(r"<footer\b.*?</footer>", html, re.S).group(0)
            self.assertIn("Sister Company", footer)
            self.assertIn("Slater Woods Design Company", footer)
            self.assertNotIn("Slater Woods", html.replace(footer, ""))
            self.assertNotContains(response, "web-site.css")
        for path in ["/admin/", "/plans/", "/submit-work/", "/clients/"]:
            self.assertEqual(self.client.get(path, HTTP_HOST=self.host).status_code, 404)

    def submit(self):
        session = self.client.session
        session["web_design_started_ts"] = time() - 5
        session.save()
        return self.client.post("/contact/", {
            "name": "Review Test", "email": "review@example.invalid", "project_type": "other",
            "source": "services_general", "message": "Connect our project intake and notifications.",
            "terms_accepted": "on",
        }, HTTP_HOST=self.host)

    def test_digital_solution_inquiry_saves_and_sends_branded_email(self):
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(WebDesignInquiry.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertIn("Slater Woods Design Company", message.body)
        self.assertIn("https://www.provosthomedesign.com/admin/", mail.outbox[0].body)
        self.assertNotIn("https://www.provosthomedesign.com/admin/", mail.outbox[1].body)

    def test_failed_email_preserves_saved_inquiry(self):
        with patch("pages.views.EmailMultiAlternatives.send", side_effect=RuntimeError("test delivery failure")):
            response = self.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WebDesignInquiry.objects.count(), 1)

    def test_csrf_and_recaptcha_still_reject_invalid_submission(self):
        response = Client(enforce_csrf_checks=True).post("/contact/", {}, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 403)
        with patch("pages.views.verify_recaptcha_v3", return_value=(False, "rejected")):
            self.assertEqual(self.submit().status_code, 400)
        self.assertFalse(WebDesignInquiry.objects.exists())

    def test_rate_limit_does_not_save_an_inquiry(self):
        with patch("pages.views._too_many_recent_submissions", return_value=True):
            self.assertEqual(self.submit().status_code, 429)
        self.assertFalse(WebDesignInquiry.objects.exists())
