from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import HouseStyle, PlanFAQ, Plans


class PublicPlanCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ranch = HouseStyle.objects.create(style_name="Ranch", slug="ranch")
        cls.plan = Plans.objects.create(
            plan_number="PHD-101",
            plan_name="The Rehoboth Ranch",
            slug="phd-101",
            square_footage=1400,
            bedrooms=3,
            bathrooms=Decimal("2.0"),
            stories=1,
            garage_stalls=2,
            house_width_in=600,
            house_depth_in=480,
            ideal_for="Single-level daily living.",
            key_features="Home office\nWalk-in pantry",
            package_contents="Floor plans\nExterior elevations",
            first_floor_primary=True,
            has_home_office=True,
            narrow_lot=True,
        )
        cls.plan.house_styles.add(cls.ranch)
        PlanFAQ.objects.create(
            plan=cls.plan,
            question="Can the garage be removed?",
            answer="Yes. The footprint and exterior would be coordinated as a modification.",
        )
        cls.other_plan = Plans.objects.create(
            plan_number="PHD-202",
            slug="phd-202",
            square_footage=2400,
            bedrooms=4,
            bathrooms=Decimal("2.5"),
            stories=2,
            garage_stalls=2,
            house_width_in=840,
            house_depth_in=600,
        )

    def test_attribute_and_dimension_filters_are_combined(self):
        response = self.client.get(
            reverse("plans:plan_list"),
            {"stories": "1", "max_width": "55", "features": ["office", "narrow-lot"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Rehoboth Ranch")
        self.assertNotContains(response, "PHD-202")
        self.assertContains(response, 'name="robots" content="noindex,follow"')

    def test_invalid_story_filter_is_ignored(self):
        response = self.client.get(reverse("plans:plan_list"), {"stories": "invalid"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plan_count"], 2)

    def test_curated_category_has_unique_content_and_matching_plan(self):
        response = self.client.get(
            reverse("plans:plan_category", args=["small-house-plans-under-1500-square-feet"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "House Plans Under 1,500 Square Feet")
        self.assertContains(response, "The Rehoboth Ranch")
        self.assertNotContains(response, "PHD-202")

    def test_plan_detail_displays_optional_merchandising_content(self):
        response = self.client.get(self.plan.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "About this house plan")
        self.assertContains(response, "Single-level daily living.")
        self.assertContains(response, "Home office")
        self.assertContains(response, "Floor plans")
        self.assertContains(response, "Can the garage be removed?")
        self.assertContains(response, '"@type": "FAQPage"')

    def test_buy_as_shown_flow_summarizes_selected_plan(self):
        response = self.client.get(
            reverse("pages:get_started"),
            {"plan": self.plan.id, "intent": "buy-as-shown"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Purchase request")
        self.assertContains(response, "The Rehoboth Ranch")
        self.assertContains(response, "Request Purchase Confirmation")

    def test_plan_finder_submits_to_catalog_filters(self):
        response = self.client.get(reverse("plans:plan_finder"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find a practical starting point")
        self.assertContains(response, 'action="/plans/"')
        self.assertContains(response, 'name="features"')

    def test_shared_comparison_works_without_session_state(self):
        response = self.client.get(
            reverse("plans:compare_plans"),
            {"plans": f"{self.plan.slug},{self.other_plan.slug},{self.plan.slug}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PHD-101")
        self.assertContains(response, "PHD-202")
        self.assertContains(response, "Share this comparison")
        self.assertNotContains(response, "Clear All")
        self.assertEqual(len(response.context["plans"]), 2)

    def test_clear_comparison_requires_post_and_clears_session(self):
        session = self.client.session
        session["comparison_plans"] = [self.plan.id]
        session.save()

        self.assertEqual(self.client.get(reverse("plans:clear_comparison")).status_code, 405)
        response = self.client.post(reverse("plans:clear_comparison"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["comparison_plans"], [])

    def test_image_sitemap_lists_available_plan_images(self):
        Plans.objects.filter(pk=self.plan.pk).update(main_image="plans/main/phd-101.jpg")

        response = self.client.get(reverse("image_sitemap"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertContains(response, self.plan.get_absolute_url())
        self.assertContains(response, "plans/main/phd-101.jpg")
        self.assertContains(response, "Front elevation of house plan PHD-101")

# Create your tests here.
