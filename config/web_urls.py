from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from config.sitemaps import WebPagesSitemap
from pages.views import web_llms_txt, web_robots_txt

urlpatterns = [
    path("", include("pages.web_urls")),
    path("robots.txt", web_robots_txt, name="robots_txt"),
    path("llms.txt", web_llms_txt, name="llms_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": {"pages": WebPagesSitemap}}, name="sitemap"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
