from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    # Authentication
    path('login/', views.client_login, name='login'),
    path('logout/', views.client_logout, name='logout'),
    path('register/', views.client_register, name='register'),
    path('password-reset/', views.password_reset_request, name='password_reset'),
    path('password-reset/done/', views.password_reset_done, name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', views.password_reset_confirm, name='password_reset_confirm'),
    
    # Client Dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile, name='profile'),
    
    # Invoices
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoice/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoice/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    
    # Payments
    path('invoice/<int:pk>/pay/', views.payment_page, name='payment_page'),
    path('payment/create-intent/', views.create_payment_intent, name='create_payment_intent'),
    path('payment/confirm/', views.payment_confirm, name='payment_confirm'),
    path('webhook/stripe/', views.stripe_webhook, name='stripe_webhook'),
    
    # Employee Features (Staff Only) - Invoices
    path('create-invoice/', views.create_invoice, name='create_invoice'),
    path('invoice/<int:pk>/send/', views.send_invoice_email, name='send_invoice'),
    
    # Client Management (Staff Only)
    path('clients/', views.client_list, name='client_list'),
    path('client/add/', views.add_client, name='add_client'),
    path('client/<int:pk>/', views.client_detail_view, name='client_detail_view'),
    path('client/<int:pk>/edit/', views.edit_client, name='edit_client'),
    path('client/<int:pk>/delete/', views.delete_client, name='delete_client'),
    
    # Employee Management (Staff Only)
    path('employees/', views.employee_list, name='employee_list'),
    path('employee/add/', views.add_employee, name='add_employee'),
    path('employee/<int:pk>/', views.employee_detail, name='employee_detail'),
    path('employee/<int:pk>/edit/', views.edit_employee, name='edit_employee'),
    path('employee/<int:pk>/delete/', views.delete_employee, name='delete_employee'),
    
    # Project Management (Staff Only)
    path('projects/', views.project_list, name='project_list'),
    path('project/new/', views.create_project, name='create_project'),
    path('project/<int:pk>/', views.project_detail, name='project_detail'),
    path('project/<int:pk>/edit/', views.edit_project, name='edit_project'),
    path('project/<int:pk>/delete/', views.delete_project, name='delete_project'),
    
    # System Settings (Staff Only)
    path('settings/', views.system_settings, name='system_settings'),
]

