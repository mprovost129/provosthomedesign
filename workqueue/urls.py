from django.urls import path
from . import views
from . import client_views
from . import booking_views

app_name = "workqueue"
urlpatterns = [
    path("work-queue/", views.queue, name="queue"),
    path("work-queue/arrivals/", views.arrivals, name="arrivals"),
    path("submit-work/", client_views.submit_work, name="submit"),
    path("submit-work/uploads/", client_views.start_upload, name="start_upload"),
    path("submit-work/uploads/<uuid:upload_id>/", client_views.upload_action, name="upload_action"),
    path("submit-work/received/<uuid:item_id>/", client_views.submitted, name="submitted"),
    path("track-work/", client_views.tracking, name="tracking"),
    path("client-access/", client_views.confirm_access, name="confirm_access"),
    path("client-access/sign-out/", client_views.client_logout, name="client_logout"),
    path("work-files/<uuid:attachment_id>/", client_views.download, name="download"),
    path("book-appointment/", booking_views.book, name="book"),
    path("book-appointment/<uuid:appointment_id>/cancel/", booking_views.cancel, name="cancel_appointment"),
]
