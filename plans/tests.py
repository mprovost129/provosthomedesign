from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from .models import HouseStyle, PlanFAQ, Plans, SavedPlanEmailReminder


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

    def test_content_readiness_identifies_missing_merchandising_fields(self):
        self.assertFalse(self.plan.is_content_ready)
        self.assertIn("overview", self.plan.content_missing_fields)
        self.assertIn("delivery details", self.plan.content_missing_fields)

        for field, _label in Plans.CONTENT_FIELD_LABELS:
            setattr(self.plan, field, f"Complete {field}")

        self.assertTrue(self.plan.is_content_ready)
        self.assertEqual(self.plan.content_missing_fields, [])

    def test_placeholder_dimensions_are_not_published_or_filterable(self):
        self.other_plan.house_width_in = 1
        self.other_plan.house_depth_in = 1
        self.other_plan.save(update_fields=["house_width_in", "house_depth_in"])

        self.assertFalse(self.other_plan.has_publishable_dimensions)
        self.assertIn("verified dimensions", self.other_plan.content_missing_fields)
        response = self.client.get(reverse("plans:plan_list"), {"max_width": "100"})
        self.assertNotContains(response, "PHD-202")

    def test_filtered_and_search_pages_canonicalize_to_catalog(self):
        catalog_url = reverse("plans:plan_list")
        expected = f'rel="canonical" href="http://testserver{catalog_url}"'

        filtered = self.client.get(catalog_url, {"beds": "3", "page": "2"})
        searched = self.client.get(reverse("plans:search"), {"q": "ranch"})

        self.assertContains(filtered, 'name="robots" content="noindex,follow"')
        self.assertContains(filtered, expected)
        self.assertContains(searched, 'name="robots" content="noindex,follow"')
        self.assertContains(searched, expected)

    def test_style_filter_canonicalizes_to_curated_category(self):
        response = self.client.get(
            reverse("plans:plan_list_by_style", args=[self.ranch.slug])
        )
        category_url = reverse("plans:plan_category", args=["ranch-house-plans"])

        self.assertContains(response, 'name="robots" content="noindex,follow"')
        self.assertContains(
            response,
            f'rel="canonical" href="http://testserver{category_url}"',
        )

    def test_curated_category_has_unique_content_and_matching_plan(self):
        response = self.client.get(
            reverse("plans:plan_category", args=["small-house-plans-under-1500-square-feet"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "House Plans Under 1,500 Square Feet")
        self.assertContains(response, "The Rehoboth Ranch")
        self.assertNotContains(response, "PHD-202")

    def test_paginated_category_keeps_its_canonical_url(self):
        category_url = reverse(
            "plans:plan_category",
            args=["small-house-plans-under-1500-square-feet"],
        )
        response = self.client.get(category_url, {"page": "2"})

        self.assertContains(response, 'name="robots" content="noindex,follow"')
        self.assertContains(
            response,
            f'rel="canonical" href="http://testserver{category_url}"',
        )

    def test_plan_detail_displays_optional_merchandising_content(self):
        response = self.client.get(self.plan.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "About this house plan")
        self.assertContains(response, "Single-level daily living.")
        self.assertContains(response, "Home office")
        self.assertContains(response, "Floor plans")
        self.assertContains(response, "Can the garage be removed?")
        self.assertContains(response, '"@type": "FAQPage"')

    def test_new_and_admin_selected_popular_labels_are_displayed(self):
        self.plan.is_popular = True
        self.plan.save(update_fields=["is_popular"])
        Plans.objects.filter(pk=self.other_plan.pk).update(
            created_date=timezone.now() - timedelta(days=61)
        )

        catalog = self.client.get(reverse("plans:plan_list"))
        detail = self.client.get(self.plan.get_absolute_url())

        self.assertContains(catalog, "New")
        self.assertContains(catalog, "Popular")
        self.assertContains(detail, "Popular")
        self.other_plan.refresh_from_db()
        self.assertFalse(self.other_plan.is_new)

    def test_recently_viewed_plans_are_shown_in_recency_order(self):
        self.other_plan.house_styles.add(self.ranch)

        self.client.get(self.plan.get_absolute_url())
        response = self.client.get(self.other_plan.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recently viewed plans")
        self.assertEqual(
            [plan.id for plan in response.context["recently_viewed_plans"]],
            [self.plan.id],
        )

    def test_unavailable_plans_are_not_public_or_recently_viewed(self):
        self.other_plan.house_styles.add(self.ranch)
        self.other_plan.is_available = False
        self.other_plan.save(update_fields=["is_available"])
        session = self.client.session
        session["recently_viewed"] = ["invalid", self.other_plan.id, self.plan.id]
        session.save()

        self.assertEqual(self.client.get(self.other_plan.get_absolute_url()).status_code, 404)
        response = self.client.get(reverse("plans:plan_list"))

        self.assertEqual(
            [plan.id for plan in response.context["recently_viewed_plans"]],
            [self.plan.id],
        )
        self.assertNotContains(response, self.other_plan.plan_number)

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

    def test_saved_plan_email_opt_in_sends_summary_and_schedules_once(self):
        session = self.client.session
        session["saved_plans"] = [self.plan.id]
        session.save()

        response = self.client.post(
            reverse("plans:email_saved_plans"),
            {"email": "buyer@example.com", "consent": "on"},
        )

        self.assertRedirects(response, reverse("plans:favorites_list"))
        reminder = SavedPlanEmailReminder.objects.get(email="buyer@example.com")
        self.assertTrue(reminder.is_active)
        self.assertEqual(list(reminder.plans.all()), [self.plan])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Your saved house plans", mail.outbox[0].subject)

    def test_saved_plan_reminder_requires_post_to_unsubscribe(self):
        reminder = SavedPlanEmailReminder.objects.create(
            email="buyer@example.com",
            next_send_at=timezone.now() + timedelta(days=7),
        )
        reminder.plans.add(self.plan)
        url = reverse("plans:saved_plan_reminder_unsubscribe", args=[reminder.token])

        self.assertEqual(self.client.get(url).status_code, 200)
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_active)
        self.client.post(url)
        reminder.refresh_from_db()
        self.assertFalse(reminder.is_active)

    def test_due_saved_plan_reminder_command_sends_once(self):
        reminder = SavedPlanEmailReminder.objects.create(
            email="buyer@example.com",
            next_send_at=timezone.now() - timedelta(minutes=1),
        )
        reminder.plans.add(self.plan)

        call_command("send_saved_plan_reminders")

        reminder.refresh_from_db()
        self.assertFalse(reminder.is_active)
        self.assertIsNotNone(reminder.sent_at)
        self.assertEqual(len(mail.outbox), 1)

# Create your tests here.
