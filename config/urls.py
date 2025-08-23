from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static
from config.sitemaps import PlanSitemap, CorePagesSitemap
from pages.views import robots_txt  # we'll add this next
from django.views.decorators.cache import cache_page
from django.contrib.sitemaps.views import sitemap

sitemaps = {
    "pages": CorePagesSitemap,
    "plans": PlanSitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("pages.urls", "pages"), namespace="pages")),
    path("plans/", include(("plans.urls", "plans"), namespace="plans")),
    path(
        "sitemap.xml",
        cache_page(60 * 60)(sitemap),
        {"sitemaps": sitemaps},
        name="sitemap",
    ),
]

# ✅ serve MEDIA in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
