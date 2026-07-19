from django.test import TestCase, override_settings


@override_settings(
    WEB_DESIGN_HOST="web.provosthomedesign.com",
    WEB_DESIGN_URL="https://web.provosthomedesign.com",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class SubdomainRoutingTests(TestCase):
    main_host = "www.provosthomedesign.com"
    web_host = "web.provosthomedesign.com"

    def test_web_subdomain_root_uses_web_design_page(self):
        response = self.client.get("/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sites and apps that actually work")
        self.assertNotContains(response, "Search Plans")

    def test_web_subdomain_pricing_uses_web_url_surface(self):
        response = self.client.get("/pricing/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Design Pricing", html=False)

    def test_main_domain_legacy_web_page_redirects_permanently(self):
        response = self.client.get("/web-design/", HTTP_HOST=self.main_host)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://web.provosthomedesign.com/")

    def test_main_domain_legacy_pricing_redirects_permanently(self):
        response = self.client.get("/pricing/", HTTP_HOST=self.main_host)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://web.provosthomedesign.com/pricing/")

    def test_main_homepage_does_not_promote_web_design(self):
        response = self.client.get("/", HTTP_HOST=self.main_host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Web Design &amp; Development")
        self.assertContains(response, "New England House Plans")

    def test_web_subdomain_does_not_expose_house_plan_catalog(self):
        response = self.client.get("/plans/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 404)

    def test_url_reversing_uses_the_active_web_urlconf(self):
        response = self.client.get("/", HTTP_HOST=self.web_host)

        self.assertContains(response, 'href="/pricing/"')
        self.assertContains(response, 'href="/#inquiry"')

    def test_regional_service_page_is_available_on_main_site(self):
        response = self.client.get(
            "/services/custom-home-design-massachusetts/",
            HTTP_HOST=self.main_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Home Design in Massachusetts")

    def test_each_host_has_a_focused_sitemap(self):
        main_response = self.client.get("/sitemap.xml", HTTP_HOST=self.main_host)
        web_response = self.client.get("/sitemap.xml", HTTP_HOST=self.web_host)

        self.assertContains(main_response, "/services/house-plan-modifications/")
        self.assertNotContains(main_response, "web.provosthomedesign.com")
        self.assertContains(web_response, "web.provosthomedesign.com")
        self.assertNotContains(web_response, "/plans/")
