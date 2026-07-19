from django.urls import path
from . import views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("get-started/", views.get_started, name="get_started"),
    path("get-started/thanks/", views.project_thanks, name="project_thanks"),
    path("contact/", views.contact, name="contact"),
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
    path("testimonials/", views.testimonials_list, name="testimonials"),
    path("testimonials/submit/", views.submit_testimonial, name="submit_testimonial"),
    path("services/", views.services, name="services"),
    path("services/<slug:service_slug>/", views.service_detail, name="service_detail"),
    path("resources/", views.resources, name="resources"),
    path("resources/<slug:resource_slug>/", views.resource_detail, name="resource_detail"),
    path("projects/", views.case_study_list, name="case_study_list"),
    path("projects/<slug:case_study_slug>/", views.case_study_detail, name="case_study_detail"),
    path("web-design/", views.web_design_legacy_redirect, name="web_design_legacy"),
    path("pricing/", views.web_pricing_legacy_redirect, name="pricing_legacy"),
]
