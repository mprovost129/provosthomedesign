from django.urls import path
from . import views
from . import client_views
from . import booking_views
from . import delivery_views
from . import health
from . import crm_views
from . import communication_views
from . import pm_views
from . import freshbooks_views

app_name = "workqueue"
urlpatterns = [
    path('work-projects/billing/', freshbooks_views.connection, name='freshbooks_connection'),
    path('work-projects/billing/callback/', freshbooks_views.callback, name='freshbooks_callback'),
    path('work-projects/billing/clients/<uuid:client_id>/', freshbooks_views.client_link, name='freshbooks_client'),
    path('work-projects/billing/proposals/<uuid:proposal_id>/', freshbooks_views.invoice_action, name='freshbooks_invoice'),
    path('work-projects/', pm_views.projects, name='projects'),
    path('work-projects/templates/', pm_views.proposal_templates, name='proposal_templates'),
    path('work-projects/templates/<uuid:template_id>/', pm_views.proposal_templates, name='proposal_template_edit'),
    path('work-projects/proposals/<uuid:proposal_id>/', pm_views.proposal_detail, name='proposal_detail'),
    path('work-projects/proposals/<uuid:proposal_id>/pdf/', pm_views.proposal_download, name='proposal_pdf'),
    path('work-projects/<uuid:project_id>/proposal/', pm_views.proposal_edit, name='proposal_new'),
    path('work-projects/<uuid:project_id>/proposal/<uuid:proposal_id>/', pm_views.proposal_edit, name='proposal_edit'),
    path('work-projects/<uuid:project_id>/', pm_views.project_detail, name='project_detail'),
    path('proposals/access/', pm_views.proposal_access, name='proposal_access'),
    path('proposals/<uuid:revision_id>/', pm_views.proposal_public, name='proposal_public'),
    path('proposals/<uuid:revision_id>/pdf/', pm_views.proposal_public_pdf, name='proposal_public_pdf'),
    path('clients/email-settings/', communication_views.communication_settings, name='communication_settings'),
    path('clients/import/', communication_views.import_clients, name='import_clients'),
    path('clients/import/<uuid:batch_id>/', communication_views.import_preview, name='import_preview'),
    path('clients/export/', communication_views.export_clients, name='export_clients'),
    path('clients/email/<uuid:message_id>/', communication_views.email_detail, name='email_detail'),
    path('clients/<uuid:client_id>/email/', communication_views.compose, name='compose_email'),
    path("clients/", crm_views.clients, name="clients"),
    path("clients/new/", crm_views.add_client, name="add_client"),
    path("clients/<uuid:client_id>/add-work/", crm_views.add_client_work, name="add_client_work"),
    path("clients/<uuid:client_id>/", crm_views.client_detail, name="client_detail"),
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
