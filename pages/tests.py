from django.test import TestCase, override_settings
from django.core.cache import cache

from .models import ProjectCaseStudy


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
        self.assertContains(main_response, "/plans/finder/")
        self.assertNotContains(main_response, "web.provosthomedesign.com")
        self.assertContains(web_response, "web.provosthomedesign.com")
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

    def test_resource_detail_has_scope_disclaimer(self):
        response = self.client.get("/resources/what-is-included-in-a-framing-plan/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What Is Included in a Residential Framing Plan?")
        self.assertContains(response, "Project-specific requirements vary")

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
