from django.urls import path
from . import views
from . import client_views
from . import booking_views
from . import delivery_views
from . import health

app_name = "workqueue"
urlpatterns = [
    path("work-queue/", views.queue, name="queue"),
    path("queue-health/", health.public_health, name="health"),
    path("work-queue/arrivals/", views.arrivals, name="arrivals"),
    path("work-queue/order/", views.ordering, name="ordering"),
    path("work-queue/deliver/<uuid:item_id>/", delivery_views.deliver_files, name="deliver_files"),
    path("completed-files/<uuid:file_id>/", delivery_views.completed_file, name="completed_file"),
    path("submit-work/", client_views.submit_work, name="submit"),
    path("submit-work/draft/", client_views.draft_action, name="draft"),
    path("submit-work/uploads/", client_views.start_upload, name="start_upload"),
    path("submit-work/uploads/<uuid:upload_id>/", client_views.upload_action, name="upload_action"),
    path("submit-work/received/<uuid:item_id>/", client_views.submitted, name="submitted"),
    path("track-work/", client_views.tracking, name="tracking"),
    path("track-work/receipts/<uuid:submission_id>/", client_views.receipt_download, name="receipt_download"),
    path("client-access/", client_views.confirm_access, name="confirm_access"),
    path("client-access/sign-out/", client_views.client_logout, name="client_logout"),
    path("work-files/<uuid:attachment_id>/", client_views.download, name="download"),
    path("book-appointment/", booking_views.book, name="book"),
    path("book-appointment/<uuid:appointment_id>/cancel/", booking_views.cancel, name="cancel_appointment"),
]
