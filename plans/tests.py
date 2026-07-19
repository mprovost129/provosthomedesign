from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import HouseStyle, Plans


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
        Plans.objects.create(
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

# Create your tests here.
