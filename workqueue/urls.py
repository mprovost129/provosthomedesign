from django.urls import path
from . import dropbox_views
from . import views
from . import pricing_views
from . import staff_search, workload
from . import operations_views
from . import staff_security_views
from . import staff_auth
from . import dashboard_views
from . import client_views
from . import intake_profile_views
from . import booking_views
from . import consultation_views
from . import delivery_views
from . import health
from . import portal_views
from . import portal_review_views
from . import portal_export_views
from . import portal_discussion_views
from . import portal_notice_views
from . import crm_views
from . import shared_contact_views
from . import communication_views
from . import pm_views
from . import project_file_views
from . import freshbooks_views
from . import time_views
from . import time_billing_views
from . import report_views
from . import checklist_views
from . import proposal_overview_views
from . import invoice_overview_views
from . import invoice_history_views

app_name = "workqueue"
urlpatterns = [
    path('work-projects/pricing/', pricing_views.estimates, name='pricing_estimates'),
    path('work-projects/pricing/new/', pricing_views.editor, name='pricing_new'),
    path('work-projects/pricing/rates/', pricing_views.rates, name='pricing_rates'),
    path('work-projects/pricing/<uuid:estimate_id>/', pricing_views.detail, name='pricing_detail'),
    path('work-projects/pricing/<uuid:estimate_id>/edit/', pricing_views.editor, name='pricing_edit'),
    path('staff/search/', staff_search.search, name='staff_search'),
    path('work-projects/workload/', workload.workload, name='workload'),
    path('staff/security/', staff_security_views.security, name='staff_security'),
    path('staff/verify/', staff_security_views.verify, name='staff_mfa_verify'),
    path('staff/password-reset/', staff_security_views.StaffPasswordResetView.as_view(), name='staff_password_reset'),
    path('staff/password-reset/sent/', staff_security_views.StaffPasswordResetDoneView.as_view(), name='staff_password_reset_done'),
    path('staff/reset/<uidb64>/<token>/', staff_security_views.StaffPasswordResetConfirmView.as_view(), name='staff_password_reset_confirm'),
    path('staff/password-reset/complete/', staff_security_views.StaffPasswordResetCompleteView.as_view(), name='staff_password_reset_complete'),
    path('dashboard/errors/', operations_views.operations, name='operations'),
    path('staff/login/', staff_auth.StaffLoginView.as_view(), name='staff_login'),
    path('staff/logout/', staff_auth.StaffLogoutView.as_view(), name='staff_logout'),
    path('dashboard/', dashboard_views.dashboard, name='dashboard'),
    path('dashboard/filing/', dashboard_views.filing_inbox, name='dashboard_filing'),
    path('dashboard/billing/check/', dashboard_views.refresh_billing, name='dashboard_billing_refresh'),
    path('work-queue/dropbox/', dropbox_views.connection, name='dropbox_connection'),
    path('work-queue/dropbox/callback/', dropbox_views.callback, name='dropbox_callback'),
    path('work-queue/<uuid:item_id>/filing/', dropbox_views.review, name='dropbox_review'),
    path('work-projects/<uuid:project_id>/invitations/', portal_notice_views.invitations, name='portal_invitations'),
    path('client-portal/projects/<uuid:project_id>/reviews/<uuid:review_id>/conversation/', portal_discussion_views.client_action, name='portal_review_discussion'),
    path('work-projects/<uuid:project_id>/reviews/<uuid:review_id>/conversation/', portal_discussion_views.staff_action, name='staff_review_discussion'),
    path('client-portal/projects/<uuid:project_id>/reviews/<uuid:review_id>/marked-plan/', portal_export_views.client_export, name='portal_marked_plan'),
    path('work-projects/<uuid:project_id>/reviews/<uuid:review_id>/marked-plan/', portal_export_views.staff_export, name='staff_marked_plan'),
    path('client-portal/projects/<uuid:project_id>/documents/<uuid:version_id>/review/', portal_review_views.start, name='start_plan_review'),
    path('client-portal/projects/<uuid:project_id>/reviews/<uuid:review_id>/', portal_review_views.review, name='portal_review'),
    path('client-portal/projects/<uuid:project_id>/reviews/<uuid:review_id>/save/', portal_review_views.save, name='save_plan_review'),
    path('client-portal/projects/<uuid:project_id>/reviews/<uuid:review_id>/submit/', portal_review_views.submit, name='submit_plan_review'),
    path('work-projects/<uuid:project_id>/reviews/', portal_review_views.staff_reviews, name='staff_plan_reviews'),
    path('work-projects/<uuid:project_id>/reviews/<uuid:review_id>/', portal_review_views.staff_review, name='staff_plan_review'),
    path('work-projects/<uuid:project_id>/reviews/<uuid:review_id>/plan/', portal_review_views.staff_plan, name='staff_review_plan'),
    path('client-portal/', portal_views.home, name='portal_home'),
    path('client-portal/sign-out/', portal_views.sign_out, name='portal_logout'),
    path('client-portal/projects/<uuid:project_id>/', portal_views.project, name='portal_project'),
    path('client-portal/projects/<uuid:project_id>/documents/<uuid:version_id>/', portal_views.document, name='portal_document'),
    path('client-portal/projects/<uuid:project_id>/proposals/<uuid:revision_id>/', portal_views.proposal, name='portal_proposal'),
    path('work-projects/<uuid:project_id>/sharing/', portal_views.manage, name='portal_manage'),
    path('work-projects/invoices/ready/', time_billing_views.ready, name='ready_to_invoice'),
    path('work-projects/<uuid:project_id>/billing/', time_billing_views.project_billing, name='project_billing'),
    path('work-projects/invoices/time/<uuid:batch_id>/', time_billing_views.batch_detail, name='time_bill_detail'),
    path('clients/<uuid:client_id>/contacts/<uuid:contact_id>/company/', shared_contact_views.link_company, name='link_contact_company'),
    path('work-projects/invoices/history/check/', invoice_history_views.check_history, name='invoice_history_check'),
    path('work-projects/invoices/import/', invoice_history_views.import_invoices, name='invoice_import'),
    path('work-projects/invoices/import/<uuid:batch_id>/', invoice_history_views.import_preview, name='invoice_import_preview'),
    path('work-projects/invoices/history/', invoice_history_views.history, name='invoice_history'),
    path('work-projects/invoices/history/<uuid:invoice_id>/', invoice_history_views.history_detail, name='invoice_history_detail'),
    path('work-projects/invoices/', invoice_overview_views.invoice_overview, name='invoice_overview'),
    path('work-projects/proposals/', proposal_overview_views.proposal_overview, name='proposal_overview'),
    path('work-projects/tasks/', checklist_views.task_list, name='task_list'),
    path('work-projects/tasks/<uuid:task_id>/', checklist_views.task_edit, name='task_edit'),
    path('work-projects/checklists/', checklist_views.checklists, name='checklists'),
    path('work-projects/checklists/<uuid:template_id>/', checklist_views.checklists, name='checklist_edit'),
    path('work-projects/<uuid:project_id>/checklist/', checklist_views.project_checklist, name='project_checklist'),
    path('work-projects/reports/', report_views.project_reports, name='project_reports'),
    path('work-queue/timer/', time_views.timer, name='timer'),
    path('work-projects/<uuid:project_id>/time/', time_views.project_time, name='project_time'),
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
    path('work-projects/<uuid:project_id>/files/add/', project_file_views.add_files, name='project_add_files'),
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
    path("submit-work/returning/", intake_profile_views.returning_client, name="returning_client"),
    path("submit-work/forget/", intake_profile_views.forget_intake, name="forget_intake"),
    path("submit-work/draft/", client_views.draft_action, name="draft"),
    path("submit-work/uploads/", client_views.start_upload, name="start_upload"),
    path("submit-work/uploads/<uuid:upload_id>/", client_views.upload_action, name="upload_action"),
    path("submit-work/received/<uuid:item_id>/", client_views.submitted, name="submitted"),
    path("track-work/", client_views.tracking, name="tracking"),
    path("track-work/receipts/<uuid:submission_id>/", client_views.receipt_download, name="receipt_download"),
    path("client-access/", client_views.confirm_access, name="confirm_access"),
    path("client-access/sign-out/", client_views.client_logout, name="client_logout"),
    path("work-files/<uuid:attachment_id>/", client_views.download, name="download"),
    path('request-consultation/', consultation_views.consultation, name='consultation'),
    path('request-consultation/resend/', consultation_views.resend, name='consultation_resend'),
    path("book-appointment/", booking_views.book, name="book"),
    path("book-appointment/<uuid:appointment_id>/cancel/", booking_views.cancel, name="cancel_appointment"),
]
