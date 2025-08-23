# config/sitemaps.py
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from typing import Iterable
from plans.models import Plans  # adjust if your model path differs

class PlanSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8
    protocol = "https"  # ensure https URLs

    def items(self) -> Iterable[Plans]: # type: ignore
        # Adjust the filter if you have a publish flag
        return Plans.objects.all().order_by("-id")[:2000]

    def lastmod(self, obj: Plans):
        return next(
            (
                getattr(obj, field)
                for field in (
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
        # list the url names you want indexed
        return [
            "pages:home",
            "plans:plan_list",
            "pages:services",
            "pages:get_started",
            "pages:about",
            "pages:contact",
        ]

    def location(self, item): # type: ignore
        return reverse(item)
