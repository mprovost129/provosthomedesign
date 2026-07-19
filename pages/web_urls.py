from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.web_design, name="web_design"),
    path("pricing/", views.pricing, name="pricing"),
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
]
