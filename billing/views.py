from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.template.loader import render_to_string
from django.core.mail import EmailMessage
from django.conf import settings
from django.db.models import Sum, Q
from decimal import Decimal
import json
import logging

from .models import Client, Invoice, Payment
from .forms import ClientRegistrationForm, ClientLoginForm, ClientProfileForm, ClientPasswordResetForm

logger = logging.getLogger(__name__)

# ==================== Authentication Views ====================

def client_login(request):
    """Client login page."""
    if request.user.is_authenticated:
        return redirect('billing:dashboard')
    
    if request.method == 'POST':
        form = ClientLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')
                next_url = request.GET.get('next', 'billing:dashboard')
                return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = ClientLoginForm()
    
    return render(request, 'billing/login.html', {'form': form})


def client_logout(request):
    """Client logout."""
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('pages:home')


def client_register(request):
    """Client registration page."""
    if request.user.is_authenticated:
        return redirect('billing:dashboard')
    
    if request.method == 'POST':
        form = ClientRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Registration successful! Welcome to your client portal.')
            return redirect('billing:dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ClientRegistrationForm()
    
    return render(request, 'billing/register.html', {'form': form})


def password_reset_request(request):
    """Password reset request page."""
    if request.method == 'POST':
        form = ClientPasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            users = form.get_users(email)
            for user in users:
                # Generate password reset token
                token = default_token_generator.make_token(user)
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                
                # Build reset URL
                reset_url = request.build_absolute_uri(
                    f'/portal/password-reset/{uid}/{token}/'
                )
                
                # Send email
                context = {
                    'user': user,
                    'reset_url': reset_url,
                    'site_name': settings.COMPANY_NAME,
                }
                subject = f'Password Reset - {settings.COMPANY_NAME}'
                message = render_to_string('billing/emails/password_reset.txt', context)
                
                email_msg = EmailMessage(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
                email_msg.send()
            
            messages.success(request, 'Password reset instructions have been sent to your email.')
            return redirect('billing:password_reset_done')
    else:
        form = ClientPasswordResetForm()
    
    return render(request, 'billing/password_reset.html', {'form': form})


def password_reset_done(request):
    """Password reset email sent confirmation."""
    return render(request, 'billing/password_reset_done.html')


def password_reset_confirm(request, uidb64, token):
    """Password reset confirmation page."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Client.objects.get(user__pk=uid).user
    except (TypeError, ValueError, OverflowError, Client.DoesNotExist):
        user = None
    
    if user is not None and default_token_generator.check_token(user, token):
        if request.method == 'POST':
            form = SetPasswordForm(user, request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, 'Password reset successful! You can now log in.')
                return redirect('billing:login')
        else:
            form = SetPasswordForm(user)
        
        return render(request, 'billing/password_reset_confirm.html', {'form': form})
    else:
        messages.error(request, 'Password reset link is invalid or has expired.')
        return redirect('billing:password_reset')


# ==================== Client Portal Views ====================

@login_required(login_url='/portal/login/')
def dashboard(request):
    """Client dashboard showing invoices and account overview."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        # Create client profile if doesn't exist
        client = Client.objects.create(user=request.user)
    
    # Get invoices
    invoices = client.invoices.all()[:10]
    
    # Calculate totals
    total_outstanding = sum(inv.get_balance_due() for inv in client.invoices.exclude(status='paid'))
    total_paid = client.invoices.filter(status='paid').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
    
    # Recent payments
    recent_payments = Payment.objects.filter(
        invoice__client=client,
        status='succeeded'
    ).order_by('-processed_at')[:5]
    
    # Count invoices by status
    pending_count = client.invoices.filter(status__in=['sent', 'overdue']).count()
    
    context = {
        'client': client,
        'invoices': invoices,
        'total_outstanding': total_outstanding,
        'total_paid': total_paid,
        'recent_payments': recent_payments,
        'pending_count': pending_count,
    }
    
    return render(request, 'billing/dashboard.html', context)


@login_required(login_url='/portal/login/')
def profile(request):
    """Client profile edit page."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        client = Client.objects.create(user=request.user)
    
    if request.method == 'POST':
        form = ClientProfileForm(request.POST, instance=client, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('billing:profile')
    else:
        form = ClientProfileForm(instance=client, user=request.user)
    
    return render(request, 'billing/profile.html', {'form': form, 'client': client})


@login_required(login_url='/portal/login/')
def invoice_list(request):
    """List all invoices for the logged-in client."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        client = Client.objects.create(user=request.user)
    
    # Filter by status
    status_filter = request.GET.get('status', '')
    invoices = client.invoices.all()
    
    if status_filter:
        invoices = invoices.filter(status=status_filter)
    
    context = {
        'client': client,
        'invoices': invoices,
        'status_filter': status_filter,
    }
    
    return render(request, 'billing/invoice_list.html', context)


@login_required(login_url='/portal/login/')
def invoice_detail(request, pk):
    """View a specific invoice."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        client = Client.objects.create(user=request.user)
    
    invoice = get_object_or_404(Invoice, pk=pk, client=client)
    
    # Get payments for this invoice
    payments = invoice.payments.filter(status='succeeded').order_by('-processed_at')
    
    context = {
        'client': client,
        'invoice': invoice,
        'payments': payments,
        'balance_due': invoice.get_balance_due(),
        'stripe_publishable_key': settings.STRIPE_PUBLISHABLE_KEY,
    }
    
    return render(request, 'billing/invoice_detail.html', context)


@login_required(login_url='/portal/login/')
def invoice_pdf(request, pk):
    """Generate and download invoice as PDF."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        return HttpResponse('Client profile not found', status=404)
    
    invoice = get_object_or_404(Invoice, pk=pk, client=client)
    
    # Generate PDF using ReportLab
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from io import BytesIO
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    
    # Company header
    elements.append(Paragraph(f"<b>{settings.COMPANY_NAME}</b>", styles['Heading1']))
    elements.append(Paragraph(settings.CONTACT_ADDRESS, styles['Normal']))
    elements.append(Paragraph(f"Phone: {settings.CONTACT_PHONE}", styles['Normal']))
    elements.append(Paragraph(f"Email: {settings.CONTACT_EMAIL}", styles['Normal']))
    elements.append(Spacer(1, 0.3*inch))
    
    # Invoice title
    elements.append(Paragraph(f"<b>INVOICE {invoice.invoice_number}</b>", styles['Heading2']))
    elements.append(Spacer(1, 0.2*inch))
    
    # Bill To section
    elements.append(Paragraph("<b>Bill To:</b>", styles['Heading3']))
    elements.append(Paragraph(str(client), styles['Normal']))
    if client.company_name:
        elements.append(Paragraph(client.company_name, styles['Normal']))
    if client.get_full_address():
        for line in client.get_full_address().split('\n'):
            elements.append(Paragraph(line, styles['Normal']))
    elements.append(Spacer(1, 0.3*inch))
    
    # Invoice details
    details_data = [
        ['Issue Date:', invoice.issue_date.strftime('%B %d, %Y')],
        ['Due Date:', invoice.due_date.strftime('%B %d, %Y')],
        ['Status:', invoice.get_status_display()],
    ]
    details_table = Table(details_data, colWidths=[2*inch, 3*inch])
    elements.append(details_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Line items table
    line_items_data = [['Description', 'Quantity', 'Unit Price', 'Total']]
    for item in invoice.line_items.all():
        line_items_data.append([
            item.description,
            str(item.quantity),
            f"${item.unit_price:,.2f}",
            f"${item.total:,.2f}"
        ])
    
    # Add subtotal, tax, total
    line_items_data.append(['', '', 'Subtotal:', f"${invoice.subtotal:,.2f}"])
    if invoice.tax_amount > 0:
        line_items_data.append(['', '', f'Tax ({invoice.tax_rate}%):', f"${invoice.tax_amount:,.2f}"])
    line_items_data.append(['', '', '<b>Total:</b>', f"<b>${invoice.total:,.2f}</b>"])
    
    if invoice.amount_paid > 0:
        line_items_data.append(['', '', 'Amount Paid:', f"${invoice.amount_paid:,.2f}"])
        line_items_data.append(['', '', '<b>Balance Due:</b>', f"<b>${invoice.get_balance_due():,.2f}</b>"])
    
    line_items_table = Table(line_items_data, colWidths=[3*inch, 1*inch, 1.5*inch, 1.5*inch])
    line_items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(line_items_table)
    
    # Notes
    if invoice.notes:
        elements.append(Spacer(1, 0.3*inch))
        elements.append(Paragraph("<b>Notes:</b>", styles['Heading3']))
        elements.append(Paragraph(invoice.notes, styles['Normal']))
    
    doc.build(elements)
    
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="invoice_{invoice.invoice_number}.pdf"'
    
    return response


# ==================== Payment Processing ====================

@login_required(login_url='/portal/login/')
def payment_page(request, pk):
    """Payment page with Stripe integration."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        client = Client.objects.create(user=request.user)
    
    invoice = get_object_or_404(Invoice, pk=pk, client=client)
    
    if invoice.get_balance_due() <= 0:
        messages.info(request, 'This invoice has been paid in full.')
        return redirect('billing:invoice_detail', pk=pk)
    
    context = {
        'client': client,
        'invoice': invoice,
        'balance_due': invoice.get_balance_due(),
        'stripe_publishable_key': settings.STRIPE_PUBLISHABLE_KEY,
    }
    
    return render(request, 'billing/payment_page.html', context)


@login_required(login_url='/portal/login/')
@require_POST
def create_payment_intent(request):
    """Create a Stripe PaymentIntent for invoice payment."""
    try:
        data = json.loads(request.body)
        invoice_id = data.get('invoice_id')
        
        client = request.user.client_profile
        invoice = get_object_or_404(Invoice, pk=invoice_id, client=client)
        
        balance_due = invoice.get_balance_due()
        
        if balance_due <= 0:
            return JsonResponse({'error': 'Invoice already paid'}, status=400)
        
        # Initialize Stripe
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        
        # Create or get Stripe customer
        if not client.stripe_customer_id:
            stripe_customer = stripe.Customer.create(
                email=request.user.email,
                name=request.user.get_full_name(),
                metadata={'client_id': client.id}
            )
            client.stripe_customer_id = stripe_customer.id
            client.save()
        
        # Create PaymentIntent
        intent = stripe.PaymentIntent.create(
            amount=int(balance_due * 100),  # Stripe uses cents
            currency='usd',
            customer=client.stripe_customer_id,
            metadata={
                'invoice_id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'client_id': client.id,
            },
            description=f'Payment for Invoice {invoice.invoice_number}'
        )
        
        # Create pending payment record
        Payment.objects.create(
            invoice=invoice,
            amount=balance_due,
            payment_method='stripe_card',
            status='pending',
            stripe_payment_intent_id=intent.id
        )
        
        return JsonResponse({
            'clientSecret': intent.client_secret,
            'paymentIntentId': intent.id
        })
        
    except Exception as e:
        logger.error(f'Error creating payment intent: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='/portal/login/')
@require_POST
def payment_confirm(request):
    """Confirm payment after Stripe confirms the PaymentIntent."""
    try:
        data = json.loads(request.body)
        payment_intent_id = data.get('payment_intent_id')
        
        # Update payment status
        payment = Payment.objects.get(stripe_payment_intent_id=payment_intent_id)
        payment.status = 'succeeded'
        payment.save()
        
        messages.success(request, 'Payment successful! Thank you.')
        
        return JsonResponse({
            'success': True,
            'redirect_url': f'/portal/invoice/{payment.invoice.id}/'
        })
        
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Payment not found'}, status=404)
    except Exception as e:
        logger.error(f'Error confirming payment: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse(status=400)
    
    # Handle the event
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        
        try:
            payment = Payment.objects.get(stripe_payment_intent_id=payment_intent['id'])
            payment.status = 'succeeded'
            payment.stripe_charge_id = payment_intent.get('latest_charge')
            payment.save()
            
            logger.info(f'Payment succeeded: {payment.payment_id}')
        except Payment.DoesNotExist:
            logger.warning(f'Payment not found for intent: {payment_intent["id"]}')
    
    elif event['type'] == 'payment_intent.payment_failed':
        payment_intent = event['data']['object']
        
        try:
            payment = Payment.objects.get(stripe_payment_intent_id=payment_intent['id'])
            payment.status = 'failed'
            payment.save()
            
            logger.error(f'Payment failed: {payment.payment_id}')
        except Payment.DoesNotExist:
            logger.warning(f'Payment not found for intent: {payment_intent["id"]}')
    
    return HttpResponse(status=200)
