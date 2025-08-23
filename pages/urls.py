from django.urls import path
from . import views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("get-started/", views.get_started, name="get_started"),
    path("contact/", views.contact, name="contact"),
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
    path("testimonials/", views.testimonials_list, name="testimonials"),
    path("services/", views.services, name="services"),

]
