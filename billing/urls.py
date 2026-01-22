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
]
