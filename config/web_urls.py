from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from config.sitemaps import WebCaseStudySitemap, WebPagesSitemap, WebServiceSitemap
from pages.views import web_llms_txt, web_robots_txt

handler400 = "pages.views.web_bad_request"
handler403 = "pages.views.web_permission_denied"
handler404 = "pages.views.web_page_not_found"
handler500 = "pages.views.web_server_error"

urlpatterns = [
    path("", include("pages.web_urls")),
    path("robots.txt", web_robots_txt, name="robots_txt"),
    path("llms.txt", web_llms_txt, name="llms_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": {"pages": WebPagesSitemap, "services": WebServiceSitemap, "work": WebCaseStudySitemap}}, name="sitemap"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
