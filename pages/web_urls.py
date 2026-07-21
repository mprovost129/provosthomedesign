from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.web_design, name="web_design"),
    path("services/", views.web_services, name="web_services"),
    path("work/", views.web_work, name="web_work"),
    path("about/", views.web_about, name="web_about"),
    path("contact/", views.web_contact, name="web_contact"),
    path("contact/thanks/", views.web_thanks, name="web_thanks"),
    path("pricing/", views.pricing, name="pricing"),
    path("terms/", views.web_terms, name="terms"),
    path("privacy/", views.web_privacy, name="privacy"),
]
