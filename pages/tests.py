from io import StringIO
from time import time

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import get_resolver, get_urlconf, set_urlconf
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict

from .forms import NewHouseForm
from .models import PricingPage, ProjectCaseStudy, ProjectInquiry, WebDesignInquiry


@override_settings(
    RECAPTCHA_ENTERPRISE_API_KEY="",
    RECAPTCHA_SECRET_KEY="",
    RECAPTCHA_PRIVATE_KEY="",
    GET_STARTED_NOTIFY_VIA_SIGNALS=False,
)
class ProjectInquiryFormTests(TestCase):
    def test_invalid_submission_retains_values_and_links_error_summary(self):
        response = self.client.post(
            "/get-started/",
            {
                "first_name": "Morgan",
                "last_name": "",
                "email": "not-an-email",
                "phone_number": "508-555-0100",
                "preferred_contact_method": "email",
                "additional_notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="form-error-summary"')
        self.assertContains(response, 'href="#id_email"')
        self.assertContains(response, 'value="Morgan"')
        self.assertContains(response, 'aria-invalid="true"')

    def test_upload_count_limit_matches_visible_guidance(self):
        files = MultiValueDict({
            "plan_files": [
                SimpleUploadedFile(
                    f"plan-{number}.pdf",
                    b"%PDF-1.4 test",
                    content_type="application/pdf",
                )
                for number in range(6)
            ]
        })
        form = NewHouseForm(
            data={
                "first_name": "Morgan",
                "last_name": "Lee",
                "email": "morgan@example.com",
                "phone_number": "508-555-0100",
                "preferred_contact_method": "email",
                "project_type": "new-home",
                "project_location": "Rehoboth, MA",
                "additional_notes": "A small addition in Massachusetts.",
                "terms_accepted": "on",
            },
            files=files,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Upload no more than 5 files", form.errors["plan_files"][0])

    def test_consultation_intent_preselects_short_call(self):
        response = self.client.get("/get-started/", {"intent": "consultation"})

        self.assertContains(response, "15-minute introductory phone call")
        self.assertTrue(response.context["form"].initial["consultation_requested"])

    def test_structured_project_answers_are_saved(self):
        response = self.client.post(
            "/get-started/",
            {
                "project_type": "addition",
                "project_location": "Swansea, MA",
                "approximate_size": "under-1000",
                "project_timeline": "3-6-months",
                "budget_range": "250k-500k",
                "consultation_requested": "on",
                "first_name": "Morgan",
                "last_name": "Lee",
                "email": "morgan@example.com",
                "phone_number": "508-555-0100",
                "preferred_contact_method": "phone",
                "terms_accepted": "on",
            },
        )

        self.assertRedirects(response, "/get-started/thanks/")
        inquiry = ProjectInquiry.objects.get()
        self.assertEqual(inquiry.project_type, "addition")
        self.assertEqual(inquiry.project_location, "Swansea, MA")
        self.assertTrue(inquiry.consultation_requested)


@override_settings(
    WEB_DESIGN_HOST="web.provosthomedesign.com",
    WEB_DESIGN_URL="https://web.provosthomedesign.com",
    RECAPTCHA_ENTERPRISE_API_KEY="",
    RECAPTCHA_SECRET_KEY="",
    RECAPTCHA_PRIVATE_KEY="",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
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
        self.assertContains(response, "Clear online")
        self.assertContains(response, "websites for local businesses")
        self.assertNotContains(response, "Search Plans")
        self.assertContains(response, 'content="Provost Home Design Web Services"')

    def test_web_metadata_does_not_inherit_residential_signals(self):
        response = self.client.get("/contact/", HTTP_HOST=self.web_host)

        self.assertContains(response, "Practical websites for local businesses")
        self.assertContains(response, '"name": "Provost Home Design Web Services"')
        self.assertContains(
            response,
            "web.provosthomedesign.com/static/images/web-og-preview.png",
            count=2,
        )
        self.assertContains(response, 'href="/static/images/web-mark.svg"')
        self.assertContains(
            response,
            '"logo": "http://web.provosthomedesign.com/static/images/web-mark.svg"',
        )
        self.assertContains(response, 'name="theme-color" content="#17211f"')
        self.assertNotContains(response, "/static/images/og-image.jpg")
        self.assertNotContains(response, "/static/images/phdlogo.svg")
        self.assertNotContains(response, "Custom &amp; stock home plans")
        self.assertNotContains(response, "facebook.com/ProvostHomeDesign")

        main_response = self.client.get("/", HTTP_HOST=self.main_host)
        self.assertContains(
            main_response,
            "www.provosthomedesign.com/static/images/og-image.jpg",
            count=2,
        )
        self.assertNotContains(main_response, "web-og-preview.png")
        self.assertContains(main_response, 'href="/static/images/phdlogo.svg"')
        self.assertContains(main_response, 'name="theme-color" content="#005ea0"')
        self.assertNotContains(main_response, "/static/images/web-mark.svg")

    def test_web_case_studies_keep_project_specific_social_images(self):
        response = self.client.get(
            "/work/j-fisk-construction/",
            HTTP_HOST=self.web_host,
        )

        self.assertContains(response, "/static/images/fisk_truck.jpeg", count=3)
        self.assertNotContains(response, "web-og-preview.png")

    def test_standalone_web_pages_are_available(self):
        expected = {
            "/services/": "The right-sized build",
            "/work/": "Built for real use",
            "/about/": "A business owner building",
            "/contact/": "What should the website help",
            "/contact/thanks/": "Inquiry received",
            "/privacy/": "Web services privacy notice",
            "/terms/": "Web services terms",
        }

        for path, text in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=self.web_host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)

    def test_web_services_explains_process_and_common_commitments(self):
        response = self.client.get("/services/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Decisions first. Build second.")
        self.assertContains(response, "Fit and discovery")
        self.assertContains(response, "Your part:", count=5)
        self.assertContains(response, "Will I own my website?")
        self.assertContains(response, "Are hosting, domains, and ongoing maintenance included?")
        self.assertContains(response, '"@type": "Service"')
        self.assertContains(response, '"@type": "FAQPage"')
        self.assertContains(response, 'data-analytics-label="services final"')

    def test_web_service_detail_pages_have_focused_content_and_schema(self):
        expected = {
            "/services/business-websites/": (
                "A clear online home for a working business.",
                "project_type=business_site&amp;source=services_business",
            ),
            "/services/website-redesigns/": (
                "Keep the value. Remove the friction.",
                "project_type=redesign&amp;source=services_redesign",
            ),
            "/services/custom-django-applications/": (
                "Software shaped around the way the business works.",
                "project_type=web_app&amp;source=services_app",
            ),
        }

        for path, (headline, contact_query) in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=self.web_host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, headline)
                self.assertContains(response, "Typical deliverables")
                self.assertContains(response, "Scope boundary")
                self.assertContains(response, contact_query, count=2)
                self.assertContains(response, '"@type": "Service"')
                self.assertContains(response, '"@type": "FAQPage"')
                self.assertContains(response, '"@type": "BreadcrumbList"')

    def test_unknown_web_service_returns_404(self):
        response = self.client.get(
            "/services/not-a-service/",
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=False)
    def test_production_web_404_uses_branded_recovery_page(self):
        response = Client().get(
            "/missing-web-page/",
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "That page is not part of this site.", status_code=404)
        self.assertContains(response, 'name="robots" content="noindex,follow"', status_code=404)
        self.assertContains(response, 'href="/services/"', status_code=404)
        self.assertContains(response, 'href="/contact/"', status_code=404)
        self.assertContains(response, "/static/images/web-mark.svg", status_code=404)
        self.assertNotContains(response, "Search Plans", status_code=404)

    def test_web_urlconf_registers_all_branded_error_handlers(self):
        resolver = get_resolver("config.web_urls")
        expected = {
            "400": "web_bad_request",
            "403": "web_permission_denied",
            "404": "web_page_not_found",
            "500": "web_server_error",
        }

        for status, function_name in expected.items():
            with self.subTest(status=status):
                handler = resolver.resolve_error_handler(status)
                self.assertEqual(handler.__name__, function_name)

        request = RequestFactory().get("/broken/", HTTP_HOST=self.web_host)
        request.session = {}
        previous_urlconf = get_urlconf()
        set_urlconf("config.web_urls")
        try:
            response = resolver.resolve_error_handler("500")(request)
        finally:
            set_urlconf(previous_urlconf)

        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "The site could not complete that request.", status_code=500)
        self.assertContains(response, "/static/images/web-mark.svg", status_code=500)

    def test_web_about_leads_with_durable_positioning_and_real_work(self):
        response = self.client.get("/about/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "One accountable point of contact")
        self.assertContains(response, "The owner-operator perspective")
        self.assertContains(response, 'href="/work/j-fisk-construction/"')
        self.assertContains(response, 'href="/work/provost-home-design-platform/"')
        self.assertContains(response, '"@type": "ProfilePage"')
        self.assertContains(response, '"@type": "Person"')
        self.assertNotContains(response, "Provost Home Design for now")
        self.assertNotContains(response, "standalone branding")

        llms_response = self.client.get("/llms.txt", HTTP_HOST=self.web_host)
        self.assertContains(llms_response, "Provost Home Design Web Services")
        self.assertNotContains(llms_response, "temporarily hosted")
        self.assertNotContains(llms_response, "standalone brand")

    def test_web_case_studies_have_detail_pages_and_schema(self):
        expected = {
            "/work/j-fisk-construction/": "What the project had to accomplish",
            "/work/provost-home-design-platform/": "A working production result",
        }

        for path, text in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=self.web_host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)
                self.assertContains(response, '"@type": "Article"')
                self.assertContains(response, '"@type": "BreadcrumbList"')

    def test_unknown_web_case_study_returns_404(self):
        response = self.client.get(
            "/work/not-a-project/",
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 404)

    def test_web_subdomain_pricing_uses_web_url_surface(self):
        response = self.client.get("/pricing/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pricing starts with a defined scope.")
        self.assertContains(response, "Public packages are not published yet.")
        self.assertContains(response, "Know what the price covers.")
        self.assertContains(response, 'data-analytics-label="pricing final"')

    def test_published_web_rate_uses_the_planning_estimate_surface(self):
        page = PricingPage.load()
        page.title = "Web Services Pricing"
        page.subtitle = "Published planning rates."
        page.is_published = True
        page.save()
        page.items.create(
            label="Additional content page",
            description="A page using the approved design system.",
            amount="250.00",
            unit_label="per page",
            show_in_calculator=True,
            default_quantity="1.0",
            is_active=True,
        )

        response = self.client.get("/pricing/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Published planning rates.")
        self.assertContains(response, "Additional content page")
        self.assertContains(response, "$250")
        self.assertContains(response, "Build a rough estimate.")
        self.assertContains(response, "Final scope and price are confirmed in writing")

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
        self.assertContains(
            response,
            'href="https://web.provosthomedesign.com/"',
        )
        self.assertContains(response, "Web Services")

    def test_web_subdomain_does_not_expose_house_plan_catalog(self):
        response = self.client.get("/plans/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 404)

    def test_url_reversing_uses_the_active_web_urlconf(self):
        response = self.client.get("/", HTTP_HOST=self.web_host)

        self.assertContains(response, 'href="/pricing/"')
        self.assertContains(response, 'href="/contact/"')

    def test_unpublished_pricing_links_to_web_contact(self):
        page = PricingPage.load()
        page.is_published = False
        page.save(update_fields=["is_published"])

        response = self.client.get("/pricing/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/contact/"')
        self.assertContains(response, 'href="/services/"')
        self.assertNotContains(response, "/get-started/")

    @override_settings(RECAPTCHA_SITE_KEY="configured-web-key")
    def test_web_contact_uses_configured_recaptcha_key(self):
        response = self.client.get("/contact/", HTTP_HOST=self.web_host)

        self.assertContains(response, "render=configured-web-key")
        self.assertContains(response, "configured\\u002Dweb\\u002Dkey")
        self.assertNotContains(response, "6LdAVUEtAAAAANkmJS6XbgPzqDf_oX4Y45sUdmDV")

    def test_service_contact_link_prefills_project_and_safe_source(self):
        services_response = self.client.get("/services/", HTTP_HOST=self.web_host)
        self.assertContains(
            services_response,
            "/contact/?project_type=redesign&amp;source=services_redesign",
        )

        response = self.client.get(
            "/contact/?project_type=redesign&source=services_redesign",
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starting point:")
        self.assertContains(response, "Redesign / Refresh")
        self.assertContains(response, 'value="redesign" selected')
        self.assertContains(response, 'name="source" value="services_redesign"')
        self.assertContains(response, 'data-analytics-source="services_redesign"')

    def test_primary_web_pages_pass_first_party_inquiry_sources(self):
        expected = {
            "/": "/contact/?source=home",
            "/work/": "/contact/?source=work",
            "/work/j-fisk-construction/": "/contact/?source=work",
            "/about/": "/contact/?source=about",
            "/pricing/": "/contact/?source=pricing",
        }

        for path, contact_url in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=self.web_host)
                self.assertContains(response, contact_url)

    def test_thank_you_conversion_requires_a_saved_inquiry_context(self):
        response = self.client.get("/contact/thanks/", HTTP_HOST=self.web_host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-analytics-page-event="web_inquiry_complete"')

    def test_web_contact_rejects_unknown_attribution_source(self):
        cache.clear()
        session = self.client.session
        session["web_design_started_ts"] = time() - 3
        session.save()

        response = self.client.post(
            "/contact/",
            {
                "name": "Alex Builder",
                "email": "alex@example.com",
                "project_type": "business_site",
                "source": "untrusted-source",
                "message": "This would otherwise be a valid inquiry.",
                "terms_accepted": "on",
            },
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The project link could not be verified")
        self.assertContains(response, 'data-analytics-source="direct"')
        self.assertNotContains(response, 'data-analytics-source="untrusted-source"')
        self.assertEqual(WebDesignInquiry.objects.count(), 0)

    def test_invalid_web_inquiry_preserves_entered_data(self):
        cache.clear()
        session = self.client.session
        session["web_design_started_ts"] = time() - 3
        session.save()

        response = self.client.post(
            "/contact/",
            {
                "name": "Alex Builder",
                "company_name": "Alex Building Co.",
                "email": "not-an-email",
                "phone": "508-555-0100",
                "current_website": "https://alexbuilding.example.com",
                "project_type": "business_site",
                "budget_range": "3k_7k",
                "timeline": "1_2_months",
                "message": "Please keep this project description.",
                "terms_accepted": "on",
            },
            HTTP_HOST=self.web_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please keep this project description.")
        self.assertContains(response, "Alex Building Co.")
        self.assertContains(response, "Your entered information has been preserved")
        self.assertContains(response, 'id="web-form-error-summary"')
        self.assertContains(response, 'href="#id_email"')
        self.assertContains(response, 'id="id_email_error"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-describedby="id_email_error"')
        self.assertContains(response, "summary.focus()")
        self.assertEqual(WebDesignInquiry.objects.count(), 0)

    def test_valid_web_inquiry_redirects_to_dedicated_thank_you_page(self):
        cache.clear()
        session = self.client.session
        session["web_design_started_ts"] = time() - 3
        session.save()

        response = self.client.post(
            "/contact/",
            {
                "name": "Alex Builder",
                "company_name": "Alex Building Co.",
                "email": "alex@example.com",
                "phone": "508-555-0100",
                "current_website": "https://alexbuilding.example.com",
                "project_type": "business_site",
                "budget_range": "3k_7k",
                "timeline": "1_2_months",
                "source": "services_business",
                "message": "We need a clearer site for our construction business.",
                "terms_accepted": "on",
            },
            HTTP_HOST=self.web_host,
        )

        self.assertRedirects(
            response,
            "/contact/thanks/",
            fetch_redirect_response=False,
        )
        self.assertEqual(WebDesignInquiry.objects.count(), 1)
        inquiry = WebDesignInquiry.objects.get()
        self.assertEqual(inquiry.company_name, "Alex Building Co.")
        self.assertEqual(inquiry.current_website, "https://alexbuilding.example.com")
        self.assertEqual(inquiry.budget_range, "3k_7k")
        self.assertEqual(inquiry.timeline, "1_2_months")
        self.assertEqual(inquiry.source, "services_business")
        self.assertEqual(len(mail.outbox), 2)
        notification = next(
            message for message in mail.outbox
            if message.subject.startswith("[Web Design]")
        )
        acknowledgment = next(
            message for message in mail.outbox
            if message.subject == "We received your web project inquiry"
        )
        self.assertIn("Alex Building Co.", notification.body)
        self.assertIn("$3,000-$7,000", notification.body)
        self.assertIn("Services - business websites", notification.body)
        self.assertEqual(acknowledgment.to, ["alex@example.com"])
        self.assertIn("Your web project inquiry has been received", acknowledgment.body)
        self.assertIn("do not email passwords", acknowledgment.body)

        thanks_response = self.client.get(
            "/contact/thanks/",
            HTTP_HOST=self.web_host,
        )
        self.assertContains(
            thanks_response,
            'data-analytics-page-event="web_inquiry_complete"',
        )
        self.assertContains(
            thanks_response,
            'data-analytics-source="services_business"',
        )
        self.assertContains(
            thanks_response,
            'data-analytics-project-type="business_site"',
        )

        refreshed_response = self.client.get(
            "/contact/thanks/",
            HTTP_HOST=self.web_host,
        )
        self.assertNotContains(
            refreshed_response,
            'data-analytics-page-event="web_inquiry_complete"',
        )

    @override_settings(DEBUG=False)
    def test_production_csp_allows_analytics_and_recaptcha_connections(self):
        response = Client().get("/", HTTP_HOST=self.web_host)

        policy = response["Content-Security-Policy"]
        self.assertIn("www.google-analytics.com", policy)
        self.assertIn("region1.google-analytics.com", policy)
        self.assertIn("www.googletagmanager.com", policy)
        self.assertIn("www.google.com", policy)
        self.assertIn("www.gstatic.com", policy)

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
        self.assertContains(main_response, "/plans/finder/")
        self.assertNotContains(main_response, "web.provosthomedesign.com")
        self.assertContains(web_response, "web.provosthomedesign.com")
        self.assertContains(web_response, "/services/")
        self.assertContains(web_response, "/services/business-websites/")
        self.assertContains(web_response, "/services/website-redesigns/")
        self.assertContains(web_response, "/services/custom-django-applications/")
        self.assertContains(web_response, "/contact/")
        self.assertContains(web_response, "/work/j-fisk-construction/")
        self.assertContains(web_response, "/work/provost-home-design-platform/")
        self.assertNotContains(web_response, "/plans/")

    def test_each_host_advertises_only_its_own_sitemaps(self):
        main_response = self.client.get("/robots.txt", HTTP_HOST=self.main_host)
        web_response = self.client.get("/robots.txt", HTTP_HOST=self.web_host)

        self.assertContains(main_response, "/image-sitemap.xml")
        self.assertNotContains(web_response, "/image-sitemap.xml")
        self.assertContains(web_response, "web.provosthomedesign.com/sitemap.xml")


class ResourcePageTests(TestCase):
    def test_resource_hub_links_to_initial_guides(self):
        response = self.client.get("/resources/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plan with fewer unknowns")
        self.assertContains(response, "/resources/stock-plan-vs-custom-home-design/")
        self.assertContains(response, "/resources/massachusetts-adu-planning-considerations/")
        self.assertContains(response, "/resources/common-reasons-building-department-comments/")

    def test_resource_detail_has_scope_disclaimer(self):
        response = self.client.get("/resources/what-is-included-in-a-framing-plan/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What Is Included in a Residential Framing Plan?")
        self.assertContains(response, "Project-specific requirements vary")
        self.assertContains(response, "Michael Provost")
        self.assertContains(response, "Last reviewed")

    def test_resource_detail_has_article_and_breadcrumb_schema(self):
        response = self.client.get(
            "/resources/massachusetts-adu-planning-considerations/",
            secure=True,
            HTTP_HOST="www.provosthomedesign.com",
        )

        self.assertContains(response, 'property="og:type"        content="article"')
        self.assertContains(response, '"@type": "Article"')
        self.assertContains(response, '"@type": "BreadcrumbList"')
        self.assertContains(response, '"@type": "Person"')
        self.assertContains(response, "https://www.provosthomedesign.com/about/")

    def test_unknown_resource_returns_404(self):
        response = self.client.get("/resources/not-a-guide/")

        self.assertEqual(response.status_code, 404)

    def test_new_regional_service_pages_are_available(self):
        adu_response = self.client.get("/services/massachusetts-adu-plans/")
        regional_response = self.client.get("/services/new-england-house-plans/")

        self.assertContains(adu_response, "Massachusetts ADU Plans")
        self.assertContains(adu_response, "ADU and Carriage House Plans")
        self.assertContains(regional_response, "New England House Plans")

    def test_resource_detail_links_to_related_service_and_collection(self):
        response = self.client.get("/resources/how-to-choose-house-plan-for-narrow-lot/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/services/house-plan-modifications/")
        self.assertContains(response, "/plans/category/narrow-lot-house-plans/")

    def test_new_regional_guides_are_available_and_in_sitemap(self):
        guide_paths = (
            "/resources/do-i-need-an-architect-for-residential-project/",
            "/resources/massachusetts-adu-planning-considerations/",
            "/resources/massachusetts-stretch-code-energy-design/",
            "/resources/common-reasons-building-department-comments/",
        )

        for path in guide_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        sitemap_response = self.client.get("/sitemap.xml")
        for path in guide_paths:
            with self.subTest(sitemap_path=path):
                self.assertContains(sitemap_response, path)


class MainSiteTrustContentTests(TestCase):
    def test_about_page_uses_project_specific_professional_guidance(self):
        response = self.client.get("/about/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Who needs to be involved in your project?")
        self.assertContains(
            response,
            "/resources/do-i-need-an-architect-for-residential-project/",
        )
        self.assertNotContains(response, "can legally prepare")

    def test_content_audit_reports_authentic_content_gaps(self):
        output = StringIO()

        call_command("audit_main_site_content", stdout=output)

        report = output.getvalue()
        self.assertIn("No available house plans", report)
        self.assertIn("No published project case studies", report)
        with self.assertRaises(CommandError):
            call_command(
                "audit_main_site_content",
                "--fail-on-gaps",
                stdout=StringIO(),
            )


class ProjectCaseStudyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.study = ProjectCaseStudy.objects.create(
            title="Compact Ranch on a Constrained Lot",
            project_type="custom-home",
            location="Rehoboth, MA",
            summary="A practical single-level home coordinated around a constrained buildable area.",
            client_objective="Create comfortable single-level living with useful storage.",
            design_challenge="Fit the program within the available width while preserving daylight.",
            solution="Organize service spaces along one side and open living toward the rear yard.",
            deliverables="Floor plans\nExterior elevations\nBuilding sections",
            outcome="A coordinated permit drawing set ready for the client's next project steps.",
            is_featured=True,
            is_published=False,
        )

    def setUp(self):
        cache.clear()

    def test_unpublished_case_study_is_private_and_not_promoted(self):
        detail_response = self.client.get(self.study.get_absolute_url())
        home_response = self.client.get("/")
        sitemap_response = self.client.get("/sitemap.xml")

        self.assertEqual(detail_response.status_code, 404)
        self.assertNotContains(home_response, self.study.title)
        self.assertNotContains(sitemap_response, self.study.get_absolute_url())

    def test_published_case_study_appears_across_public_surfaces(self):
        self.study.is_published = True
        self.study.save(update_fields=["is_published", "updated_at"])

        detail_response = self.client.get(self.study.get_absolute_url())
        home_response = self.client.get("/")
        resources_response = self.client.get("/resources/")
        sitemap_response = self.client.get("/sitemap.xml")

        self.assertContains(detail_response, "What the project needed")
        self.assertContains(detail_response, "Floor plans")
        self.assertContains(detail_response, '"@type":"Article"')
        self.assertContains(home_response, self.study.title)
        self.assertContains(resources_response, "View project studies")
        self.assertContains(sitemap_response, self.study.get_absolute_url())

    def test_published_project_image_is_in_image_sitemap(self):
        ProjectCaseStudy.objects.filter(pk=self.study.pk).update(
            is_published=True,
            hero_image="projects/hero/compact-ranch.jpg",
        )

        response = self.client.get("/image-sitemap.xml")

        self.assertContains(response, self.study.get_absolute_url())
        self.assertContains(response, "projects/hero/compact-ranch.jpg")

    def test_paginated_project_index_is_noindex_with_clean_canonical(self):
        self.study.is_published = True
        self.study.save(update_fields=["is_published", "updated_at"])

        response = self.client.get("/projects/", {"page": "2"})

        self.assertContains(response, 'name="robots" content="noindex,follow"')
        self.assertContains(
            response,
            'rel="canonical" href="http://testserver/projects/"',
        )
