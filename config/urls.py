from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("pages.urls", "pages"), namespace="pages")),
    path("plans/", include(("plans.urls", "plans"), namespace="plans")),
]

# ✅ serve MEDIA in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
