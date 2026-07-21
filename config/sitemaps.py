# config/sitemaps.py
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from typing import Iterable
from plans.models import Plans  # adjust if your model path differs
from plans.views import CATEGORY_PAGES
from pages.models import ProjectCaseStudy
from pages.views import RESOURCE_ARTICLES, WEB_CASE_STUDIES, WEB_SERVICE_PAGES

class PlanSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8
    protocol = "https"  # ensure https URLs

    def items(self) -> Iterable[Plans]: # type: ignore
        return Plans.objects.filter(is_available=True).order_by("-id")[:2000]

    def lastmod(self, obj: Plans):
        return next(
            (
                getattr(obj, field)
                for field in (
                    "modified_date",
                    "updated_at",
                    "modified",
                    "updated",
                    "created_at",
                    "created",
                )
                if hasattr(obj, field)
            ),
            None,
        )

class CorePagesSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6
    protocol = "https"

    def items(self):
        items = [
            "pages:home",
            "plans:plan_list",
            "plans:plan_finder",
            "pages:services",
            "pages:get_started",
            "pages:about",
            "pages:contact",
            "pages:testimonials",
            "pages:resources",
        ]
        if ProjectCaseStudy.objects.filter(is_published=True).exists():
            items.append("pages:case_study_list")
        return items

    def location(self, item): # type: ignore
        return reverse(item)


class ServicePagesSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7
    protocol = "https"

    def items(self):
        return [
            "custom-home-design-massachusetts",
            "custom-home-design-rhode-island",
            "house-plan-modifications",
            "additions-and-renovations",
            "residential-framing-plans",
            "permit-ready-house-plans",
            "builder-contractor-plan-services",
            "massachusetts-adu-plans",
            "new-england-house-plans",
        ]

    def location(self, item):
        return reverse("pages:service_detail", args=[item])


class WebPagesSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6
    protocol = "https"

    def items(self):
        return [
            "pages:web_design",
            "pages:web_services",
            "pages:web_region",
            "pages:web_work",
            "pages:web_about",
            "pages:web_contact",
            "pages:pricing",
            "pages:terms",
            "pages:privacy",
        ]

    def location(self, item):
        return reverse(item)


class WebCaseStudySitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7
    protocol = "https"

    def items(self):
        return list(WEB_CASE_STUDIES)

    def location(self, item):
        return reverse("pages:web_case_study", args=[item])


class WebServiceSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7
    protocol = "https"

    def items(self):
        return list(WEB_SERVICE_PAGES)

    def location(self, item):
        return reverse("pages:web_service_detail", args=[item])


class PlanCategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7
    protocol = "https"

    def items(self):
        return list(CATEGORY_PAGES)

    def location(self, item):
        return reverse("plans:plan_category", args=[item])


class ResourceSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6
    protocol = "https"

    def items(self):
        return list(RESOURCE_ARTICLES)

    def location(self, item):
        return reverse("pages:resource_detail", args=[item])


class CaseStudySitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7
    protocol = "https"

    def items(self):
        return ProjectCaseStudy.objects.filter(is_published=True)

    def lastmod(self, item):
        return item.updated_at
