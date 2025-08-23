from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.cache import cache_page
from django.contrib.sitemaps.views import sitemap as sitemap_view

from config.sitemaps import PlanSitemap, CorePagesSitemap
from pages.views import robots_txt

sitemaps = {
    "pages": CorePagesSitemap,
    "plans": PlanSitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),

    # Root site pages & plans
    path("", include("pages.urls")),          # requires app_name="pages" in pages/urls.py
    path("plans/", include("plans.urls")),    # requires app_name="plans" in plans/urls.py

    # SEO endpoints
    path("robots.txt", robots_txt, name="robots_txt"),
    path(
        "sitemap.xml",
        cache_page(60 * 60)(sitemap_view),    # cache for 1 hour
        {"sitemaps": sitemaps},
        name="sitemap",
    ),
]

# Serve media only in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
