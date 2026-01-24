from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.mail import EmailMessage
from django.conf import settings
from django.db.models import Sum, Q
from decimal import Decimal
import json
import logging

from core.utils import verify_recaptcha_v3
from .models import Client, Employee, Invoice, Payment, InvoiceTemplate, InvoiceLineItem, ProposalLineItem, Project, Proposal
from .forms import (
    ClientRegistrationForm, 
    ClientLoginForm, 
    ClientProfileForm, 
    ClientPasswordResetForm,
    InvoiceForm,
    InvoiceLineItemFormSet,
    ClientForm,
    EmployeeForm
)

logger = logging.getLogger(__name__)


def send_client_welcome_email(request, client):
    """
    Create portal account for client and send welcome email with credentials.
    Returns True if successful, False otherwise.
    """
    try:
        from django.contrib.auth.models import User
        from django.core.mail import EmailMultiAlternatives
        from .models import SystemSettings
        import secrets
        import string
        
        # Check if client already has a user account
        if client.user:
            # Resend with existing credentials (generate new temp password)
            user = client.user
            alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
            temp_password = ''.join(secrets.choice(alphabet) for i in range(12))
            user.set_password(temp_password)
            user.save()
        else:
            # Create new user account
            alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
            temp_password = ''.join(secrets.choice(alphabet) for i in range(12))
            
            # Generate username from email (before @)
            username = client.email.split('@')[0]
            # Ensure username is unique
            base_username = username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            
            # Create the user
            user = User.objects.create_user(
                username=username,
                email=client.email,
                first_name=client.first_name,
                last_name=client.last_name,
                password=temp_password
            )
            user.save()
            
            # Link user to client
            client.user = user
            client.save()
        
        # Send welcome email
        settings_obj = SystemSettings.load()
        portal_url = request.build_absolute_uri('/portal/login/')
        
        context = {
            'client': client,
            'user': user,
            'temp_password': temp_password,
            'portal_url': portal_url,
            'company_name': settings_obj.company_name,
            'company_email': settings_obj.company_email,
            'company_phone': settings_obj.company_phone,
        }
        
        html_content = render_to_string('billing/emails/client_welcome_email.html', context)
        text_content = render_to_string('billing/emails/client_welcome_email.txt', context)
        
        email = EmailMultiAlternatives(
            subject=f'Welcome to {settings_obj.company_name} - Your Client Portal Access',
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[client.email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send client welcome email: {str(e)}")
        return False


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
        # reCAPTCHA v3 verification (enforced if secret is configured)
        recaptcha_ok, score = verify_recaptcha_v3(request)
        if not recaptcha_ok:
            messages.error(request, 'Security verification failed. Please try again.')
            form = ClientRegistrationForm(request.POST)
        else:
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
    
    # Pass reCAPTCHA site key to template
    recaptcha_site_key = (getattr(settings, 'RECAPTCHA_SITE_KEY', '') or '').strip()
    return render(request, 'billing/register.html', {
        'form': form,
        'recaptcha_site_key': recaptcha_site_key
    })


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
    
    # Recent proposals
    recent_proposals = Proposal.objects.filter(client=client).order_by('-created_at')[:5]
    
    # Recent plan files
    from .models import ClientPlanFile
    recent_plan_files = ClientPlanFile.objects.filter(client=client).order_by('-uploaded_at')[:5]
    
    context = {
        'client': client,
        'invoices': invoices,
        'total_outstanding': total_outstanding,
        'total_paid': total_paid,
        'recent_payments': recent_payments,
        'pending_count': pending_count,
        'recent_proposals': recent_proposals,
        'recent_plan_files': recent_plan_files,
    }
    
    return render(request, 'billing/dashboard.html', context)


@login_required(login_url='/portal/login/')
def plan_files(request):
    """View for clients to see and download their plan files from Dropbox."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        messages.error(request, 'Client profile not found.')
        return redirect('billing:dashboard')
    
    # Get all active plan files for this client, organized by project
    from billing.models import ClientPlanFile
    
    plan_files = ClientPlanFile.objects.filter(
        client=client,
        is_active=True
    ).select_related('project', 'uploaded_by').order_by('-uploaded_at')
    
    # Group files by project
    files_by_project = {}
    for file in plan_files:
        project_key = file.project.job_name if file.project else 'General Files'
        if project_key not in files_by_project:
            files_by_project[project_key] = []
        files_by_project[project_key].append(file)
    
    context = {
        'client': client,
        'plan_files': plan_files,
        'files_by_project': files_by_project,
    }
    
    return render(request, 'billing/plan_files.html', context)


@login_required(login_url='/portal/login/')
def profile(request):
    """Client profile edit page."""
    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        client = Client.objects.create(user=request.user)
    
    if request.method == 'POST':
        form = ClientProfileForm(request.POST, request.FILES, instance=client, user=request.user)
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
    
    # Track when client views invoice (only track first view)
    if not request.user.is_staff and invoice.status == 'sent' and not invoice.viewed_date:
        invoice.viewed_date = timezone.now()
        invoice.save()
    
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


# ==================== Employee Portal Views (Staff Only) ====================

@staff_member_required(login_url='/portal/login/')
def create_invoice(request):
    """Create a new invoice (staff only)."""
    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        formset = InvoiceLineItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            invoice = form.save(commit=False)
            
            # If a template was selected, populate from template
            template = form.cleaned_data.get('template')
            if template and not invoice.description:
                invoice.description = template.default_description
                invoice.notes = template.default_notes
                invoice.tax_rate = template.default_tax_rate
            
            invoice.save()
            
            # Save line items
            formset.instance = invoice
            line_items = formset.save(commit=False)
            for item in line_items:
                item.total = item.quantity * item.unit_price
                item.save()
            
            # Calculate totals
            invoice.calculate_totals()
            
            messages.success(request, f'Invoice {invoice.invoice_number} created successfully!')
            return redirect('billing:invoice_detail', pk=invoice.pk)
    else:
        form = InvoiceForm()
        formset = InvoiceLineItemFormSet()
    
    context = {
        'form': form,
        'formset': formset,
        'templates': InvoiceTemplate.objects.filter(is_active=True),
    }
    return render(request, 'billing/create_invoice.html', context)


@staff_member_required(login_url='/portal/login/')
def client_list(request):
    """View all clients (staff only)."""
    from django.db.models import Sum, Count, Q
    
    clients = Client.objects.annotate(
        invoice_count=Count('invoices'),
        total_billed=Sum('invoices__total'),
        total_paid=Sum('invoices__amount_paid'),
        outstanding=Sum('invoices__total') - Sum('invoices__amount_paid')
    ).order_by('-created_at')
    
    # Calculate overall totals
    totals = Client.objects.aggregate(
        total_billed=Sum('invoices__total'),
        total_outstanding=Sum('invoices__total') - Sum('invoices__amount_paid')
    )
    
    context = {
        'clients': clients,
        'total_billed': totals['total_billed'] or Decimal('0.00'),
        'total_outstanding': totals['total_outstanding'] or Decimal('0.00'),
    }
    return render(request, 'billing/client_list.html', context)



@staff_member_required(login_url='/portal/login/')
def send_invoice_email(request, pk):
    """Send invoice via email (staff only)."""
    from django.core.mail import EmailMultiAlternatives
    from django.urls import reverse
    
    invoice = get_object_or_404(Invoice, pk=pk)
    
    if invoice.status == 'paid':
        messages.warning(request, 'This invoice has already been paid.')
        return redirect('billing:invoice_detail', pk=invoice.pk)
    
    if invoice.status == 'cancelled':
        messages.warning(request, 'This invoice has been cancelled.')
        return redirect('billing:invoice_detail', pk=invoice.pk)
    
    if request.method == 'POST':
        try:
            # Build invoice and payment URLs
            invoice_url = request.build_absolute_uri(
                reverse('billing:invoice_detail', kwargs={'pk': invoice.pk})
            )
            payment_url = request.build_absolute_uri(
                invoice.get_public_payment_url()
            )
            
            # Prepare email context
            email_context = {
                'invoice': invoice,
                'client': invoice.client,
                'invoice_url': invoice_url,
                'payment_url': payment_url,
                'company_name': settings.COMPANY_NAME,
                'contact_email': settings.CONTACT_EMAIL,
                'contact_phone': settings.CONTACT_PHONE,
                'is_reminder': invoice.email_sent_count > 0,
            }
            
            # Render email templates
            if invoice.email_sent_count > 0:
                subject = f'Reminder: Invoice {invoice.invoice_number} - Payment Due'
            else:
                subject = f'Invoice {invoice.invoice_number} from {settings.COMPANY_NAME}'
            
            text_content = render_to_string('billing/emails/invoice_notification.txt', email_context)
            html_content = render_to_string('billing/emails/invoice_notification.html', email_context)
            
            # Create email
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[invoice.client.email],
                reply_to=[settings.CONTACT_EMAIL]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            # Update invoice status
            invoice.mark_as_sent()
            
            if invoice.email_sent_count == 1:
                messages.success(request, f'Invoice {invoice.invoice_number} sent to {invoice.client.email}')
            else:
                messages.success(request, f'Reminder sent for invoice {invoice.invoice_number} to {invoice.client.email}')
            return redirect('billing:invoice_detail', pk=invoice.pk)
            
        except Exception as e:
            logger.error(f"Failed to send invoice {invoice.invoice_number}: {str(e)}")
            messages.error(request, f'Failed to send invoice email: {str(e)}')
            return redirect('billing:invoice_detail', pk=invoice.pk)
    
    context = {
        'invoice': invoice,
        'is_reminder': invoice.email_sent_count > 0,
    }
    return render(request, 'billing/confirm_send_invoice.html', context)


# ==================== Client Management Views (Staff Only) ====================

@staff_member_required(login_url='/portal/login/')
def add_client(request):
    """Add a new client to the database (staff only)."""
    if request.method == 'POST':
        form = ClientForm(request.POST, request.FILES)
        if form.is_valid():
            client = form.save()
            
            # Check if welcome email should be sent
            if form.cleaned_data.get('send_welcome_email'):
                success = send_client_welcome_email(request, client)
                if success:
                    messages.success(request, f'Client {client.get_full_name()} added successfully! Welcome email sent to {client.email}')
                else:
                    messages.warning(request, f'Client {client.get_full_name()} added successfully, but welcome email failed to send.')
            else:
                messages.success(request, f'Client {client.get_full_name()} added successfully!')
            
            return redirect('billing:client_detail_view', pk=client.pk)
    else:
        form = ClientForm()
    
    context = {'form': form, 'title': 'Add New Client'}
    return render(request, 'billing/client_form.html', context)


@staff_member_required(login_url='/portal/login/')
def edit_client(request, pk):
    """Edit an existing client (staff only)."""
    client = get_object_or_404(Client, pk=pk)
    
    if request.method == 'POST':
        form = ClientForm(request.POST, request.FILES, instance=client)
        if form.is_valid():
            client = form.save()
            
            # Check if welcome email should be sent/resent
            if form.cleaned_data.get('send_welcome_email'):
                success = send_client_welcome_email(request, client)
                if success:
                    messages.success(request, f'Client {client.get_full_name()} updated successfully! Portal access email sent to {client.email}')
                else:
                    messages.warning(request, f'Client {client.get_full_name()} updated successfully, but email failed to send.')
            else:
                messages.success(request, f'Client {client.get_full_name()} updated successfully!')
            
            return redirect('billing:client_detail_view', pk=client.pk)
    else:
        form = ClientForm(instance=client)
    
    context = {'form': form, 'client': client, 'title': 'Edit Client'}
    return render(request, 'billing/client_form.html', context)


@staff_member_required(login_url='/portal/login/')
def client_detail_view(request, pk):
    """View detailed information about a client (staff only)."""
    from django.db.models import Sum, Count
    
    client = get_object_or_404(Client, pk=pk)
    
    # Get invoice statistics
    invoices = client.invoices.all().order_by('-issue_date')
    stats = client.invoices.aggregate(
        total_invoices=Count('id'),
        total_billed=Sum('total'),
        total_paid=Sum('amount_paid')
    )
    
    outstanding = (stats['total_billed'] or Decimal('0.00')) - (stats['total_paid'] or Decimal('0.00'))
    
    context = {
        'client': client,
        'invoices': invoices[:10],  # Last 10 invoices
        'total_invoices': stats['total_invoices'] or 0,
        'total_billed': stats['total_billed'] or Decimal('0.00'),
        'total_paid': stats['total_paid'] or Decimal('0.00'),
        'outstanding': outstanding,
    }
    return render(request, 'billing/client_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def delete_client(request, pk):
    """Delete a client (staff only)."""
    client = get_object_or_404(Client, pk=pk)
    
    if request.method == 'POST':
        client_name = client.get_full_name()
        client.delete()
        messages.success(request, f'Client {client_name} deleted successfully!')
        return redirect('billing:client_list')
    
    context = {'client': client}
    return render(request, 'billing/client_confirm_delete.html', context)


# ==================== Employee Management Views (Staff Only) ====================

@staff_member_required(login_url='/portal/login/')
def employee_list(request):
    """View all employees (staff only)."""
    employees = Employee.objects.all().order_by('status', 'last_name', 'first_name')
    
    context = {'employees': employees}
    return render(request, 'billing/employee_list.html', context)


@staff_member_required(login_url='/portal/login/')
def add_employee(request):
    """Add a new employee (staff only)."""
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            employee = form.save(commit=False)
            
            # Create user account if one wasn't selected
            if not employee.user_id:
                from django.contrib.auth.models import User
                import secrets
                import string
                
                # Generate a random temporary password
                alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
                temp_password = ''.join(secrets.choice(alphabet) for i in range(12))
                
                # Create username from email (before @)
                username = employee.email.split('@')[0]
                # Ensure username is unique
                base_username = username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1
                
                # Create the user
                user = User.objects.create_user(
                    username=username,
                    email=employee.email,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    password=temp_password
                )
                user.is_staff = True  # Give staff access
                user.save()
                
                employee.user = user
                employee.save()
                
                # Send welcome email with credentials
                from django.core.mail import EmailMultiAlternatives
                from django.template.loader import render_to_string
                from .models import SystemSettings
                
                settings_obj = SystemSettings.load()
                portal_url = request.build_absolute_uri('/portal/login/')
                
                context = {
                    'employee': employee,
                    'user': user,
                    'temp_password': temp_password,
                    'portal_url': portal_url,
                    'company_name': settings_obj.company_name,
                    'company_email': settings_obj.company_email,
                }
                
                html_content = render_to_string('billing/emails/employee_welcome_email.html', context)
                text_content = render_to_string('billing/emails/employee_welcome_email.txt', context)
                
                email = EmailMultiAlternatives(
                    subject=f'Welcome to {settings_obj.company_name} - Portal Access',
                    body=text_content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[employee.email],
                )
                email.attach_alternative(html_content, "text/html")
                
                try:
                    email.send()
                    messages.success(request, f'Employee {employee.get_full_name()} added successfully! Welcome email sent to {employee.email}')
                except Exception as e:
                    messages.warning(request, f'Employee added, but email failed to send: {str(e)}')
            else:
                employee.save()
                messages.success(request, f'Employee {employee.get_full_name()} added successfully!')
            
            return redirect('billing:employee_detail', pk=employee.pk)
    else:
        form = EmployeeForm()
    
    context = {'form': form, 'title': 'Add New Employee'}
    return render(request, 'billing/employee_form.html', context)


@staff_member_required(login_url='/portal/login/')
def edit_employee(request, pk):
    """Edit an existing employee (staff only)."""
    employee = get_object_or_404(Employee, pk=pk)
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f'Employee {employee.get_full_name()} updated successfully!')
            return redirect('billing:employee_detail', pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee)
    
    context = {'form': form, 'employee': employee, 'title': 'Edit Employee'}
    return render(request, 'billing/employee_form.html', context)


@staff_member_required(login_url='/portal/login/')
def employee_detail(request, pk):
    """View detailed information about an employee (staff only)."""
    employee = get_object_or_404(Employee, pk=pk)
    
    context = {'employee': employee}
    return render(request, 'billing/employee_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def delete_employee(request, pk):
    """Delete an employee (staff only)."""
    employee = get_object_or_404(Employee, pk=pk)
    
    if request.method == 'POST':
        employee_name = employee.get_full_name()
        employee.delete()
        messages.success(request, f'Employee {employee_name} deleted successfully!')
        return redirect('billing:employee_list')
    
    context = {'employee': employee}
    return render(request, 'billing/employee_confirm_delete.html', context)


@staff_member_required(login_url='/portal/login/')
def system_settings(request):
    """System settings page (staff only)."""
    from .models import SystemSettings
    from .forms import SystemSettingsForm
    
    settings = SystemSettings.load()
    
    if request.method == 'POST':
        form = SystemSettingsForm(request.POST, request.FILES, instance=settings)
        if form.is_valid():
            settings = form.save(commit=False)
            settings.updated_by = request.user
            settings.save()
            messages.success(request, 'System settings updated successfully!')
            return redirect('billing:system_settings')
    else:
        form = SystemSettingsForm(instance=settings)
    
    context = {
        'form': form,
        'settings': settings,
    }
    return render(request, 'billing/system_settings.html', context)


# ==================== PROJECT MANAGEMENT VIEWS ====================

@staff_member_required(login_url='/portal/login/')
def project_list(request):
    """List all projects with filtering and search (staff only)."""
    from .models import Project
    
    projects = Project.objects.select_related('client', 'created_by').all()
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        projects = projects.filter(
            models.Q(job_number__icontains=search_query) |
            models.Q(job_name__icontains=search_query) |
            models.Q(client__first_name__icontains=search_query) |
            models.Q(client__last_name__icontains=search_query) |
            models.Q(client__company_name__icontains=search_query)
        )
    
    # Status filter
    status_filter = request.GET.get('status', '')
    if status_filter:
        projects = projects.filter(status=status_filter)
    
    # Billing type filter
    billing_type_filter = request.GET.get('billing_type', '')
    if billing_type_filter:
        projects = projects.filter(billing_type=billing_type_filter)
    
    # Client filter
    client_filter = request.GET.get('client', '')
    if client_filter:
        projects = projects.filter(client_id=client_filter)
    
    context = {
        'projects': projects,
        'search_query': search_query,
        'status_filter': status_filter,
        'billing_type_filter': billing_type_filter,
        'client_filter': client_filter,
        'status_choices': Project.STATUS_CHOICES,
        'billing_type_choices': Project.BILLING_TYPE_CHOICES,
    }
    return render(request, 'billing/project_list.html', context)


@staff_member_required(login_url='/portal/login/')
def project_detail(request, pk):
    """View detailed information about a project (staff only)."""
    from .models import Project
    project = get_object_or_404(Project, pk=pk)
    
    context = {'project': project}
    return render(request, 'billing/project_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def create_project(request):
    """Create a new project (staff only)."""
    from .forms import ProjectForm
    
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            messages.success(request, f'Project {project.job_number} created successfully!')
            return redirect('billing:project_detail', pk=project.pk)
    else:
        form = ProjectForm()
    
    context = {'form': form, 'title': 'New Project'}
    return render(request, 'billing/project_form.html', context)


@staff_member_required(login_url='/portal/login/')
def edit_project(request, pk):
    """Edit an existing project (staff only)."""
    from .models import Project
    from .forms import ProjectForm
    
    project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, f'Project {project.job_number} updated successfully!')
            return redirect('billing:project_detail', pk=project.pk)
    else:
        form = ProjectForm(instance=project)
    
    context = {
        'form': form,
        'project': project,
        'title': f'Edit Project {project.job_number}'
    }
    return render(request, 'billing/project_form.html', context)


@staff_member_required(login_url='/portal/login/')
def delete_project(request, pk):
    """Delete a project (staff only)."""
    from .models import Project
    project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        job_number = project.job_number
        project.delete()
        messages.success(request, f'Project {job_number} deleted successfully!')
        return redirect('billing:project_list')
    
    context = {'project': project}
    return render(request, 'billing/project_confirm_delete.html', context)


@staff_member_required(login_url='/portal/login/')
def delete_invoice(request, pk):
    """Delete an invoice (staff only)."""
    invoice = get_object_or_404(Invoice, pk=pk)
    
    if request.method == 'POST':
        invoice_number = invoice.invoice_number
        invoice.delete()
        messages.success(request, f'Invoice {invoice_number} deleted successfully!')
        return redirect('billing:invoice_list')
    
    context = {'invoice': invoice}
    return render(request, 'billing/invoice_confirm_delete.html', context)


# ============================================================================
# PROPOSAL VIEWS
# ============================================================================

@staff_member_required(login_url='/portal/login/')
def proposal_list(request):
    """View all proposals (staff only)."""
    from .models import Proposal
    from django.db.models import Q
    
    proposals = Proposal.objects.select_related('client', 'project').all()
    
    # Filter by status
    status_filter = request.GET.get('status')
    if status_filter:
        proposals = proposals.filter(status=status_filter)
    
    # Filter by client
    client_filter = request.GET.get('client')
    if client_filter:
        proposals = proposals.filter(client_id=client_filter)
    
    # Search
    search_query = request.GET.get('search')
    if search_query:
        proposals = proposals.filter(
            Q(proposal_number__icontains=search_query) |
            Q(title__icontains=search_query) |
            Q(client__first_name__icontains=search_query) |
            Q(client__last_name__icontains=search_query) |
            Q(client__company_name__icontains=search_query)
        )
    
    # Get distinct clients for filter dropdown
    clients = Client.objects.filter(proposals__isnull=False).distinct().order_by('last_name', 'first_name')
    
    context = {
        'proposals': proposals,
        'clients': clients,
        'status_filter': status_filter,
        'client_filter': client_filter,
        'search_query': search_query,
    }
    return render(request, 'billing/proposal_list.html', context)


@staff_member_required(login_url='/portal/login/')
def proposal_detail(request, pk):
    """View detailed information about a proposal (staff/client)."""
    from .models import Proposal
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    # Staff can view all proposals, clients can only view their own
    if not request.user.is_staff:
        if not hasattr(request.user, 'client_profile') or proposal.client != request.user.client_profile:
            messages.error(request, 'You do not have permission to view this proposal.')
            return redirect('billing:dashboard')
    
    # Track when client views proposal
    if not request.user.is_staff and proposal.status == 'sent' and not proposal.viewed_date:
        proposal.status = 'viewed'
        proposal.viewed_date = timezone.now()
        proposal.save()
    
    context = {'proposal': proposal}
    return render(request, 'billing/proposal_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def create_proposal(request):
    """Create a new proposal (staff only)."""
    from .forms import ProposalForm, ProposalLineItemFormSet
    from datetime import timedelta
    
    if request.method == 'POST':
        form = ProposalForm(request.POST)
        formset = ProposalLineItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            proposal = form.save(commit=False)
            proposal.created_by = request.user
            
            # Handle optional project field - set to None if empty
            if not proposal.project_id:
                proposal.project = None
            
            proposal.save()
            
            # Save line items
            formset.instance = proposal
            formset.save()
            
            # Recalculate totals
            proposal.calculate_totals()
            
            messages.success(request, f'Proposal {proposal.proposal_number} created successfully!')
            return redirect('billing:proposal_detail', pk=proposal.pk)
        else:
            # Display form errors
            if not form.is_valid():
                for field, errors in form.errors.items():
                    field_name = form.fields[field].label if field in form.fields else field
                    for error in errors:
                        messages.error(request, f'{field_name}: {error}')
            if not formset.is_valid():
                for i, form_errors in enumerate(formset.errors):
                    if form_errors:
                        for field, errors in form_errors.items():
                            if field != '__all__':
                                messages.error(request, f'Line item {i+1} - {field}: {errors[0] if isinstance(errors, list) else errors}')
    else:
        # Set default valid_until to 30 days from now
        initial_data = {
            'issue_date': timezone.now().date(),
            'valid_until': timezone.now().date() + timedelta(days=30)
        }
        form = ProposalForm(initial=initial_data)
        formset = ProposalLineItemFormSet()
    
    context = {
        'form': form,
        'formset': formset,
        'title': 'Create New Proposal'
    }
    return render(request, 'billing/proposal_form.html', context)


@staff_member_required(login_url='/portal/login/')
def edit_proposal(request, pk):
    """Edit an existing proposal (staff only)."""
    from .models import Proposal
    from .forms import ProposalForm, ProposalLineItemFormSet
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    # Don't allow editing accepted or rejected proposals
    if proposal.status in ['accepted', 'rejected']:
        messages.warning(request, f'Cannot edit {proposal.get_status_display().lower()} proposals.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if request.method == 'POST':
        form = ProposalForm(request.POST, instance=proposal)
        formset = ProposalLineItemFormSet(request.POST, instance=proposal)
        
        if form.is_valid() and formset.is_valid():
            proposal = form.save()
            formset.save()
            
            # Recalculate totals
            proposal.calculate_totals()
            
            messages.success(request, f'Proposal {proposal.proposal_number} updated successfully!')
            return redirect('billing:proposal_detail', pk=proposal.pk)
    else:
        form = ProposalForm(instance=proposal)
        formset = ProposalLineItemFormSet(instance=proposal)
    
    context = {
        'form': form,
        'formset': formset,
        'proposal': proposal,
        'title': f'Edit Proposal {proposal.proposal_number}'
    }
    return render(request, 'billing/proposal_form.html', context)


@staff_member_required(login_url='/portal/login/')
def duplicate_proposal(request, pk):
    """Duplicate an existing proposal (staff only)."""
    from .models import Proposal
    
    original = get_object_or_404(Proposal, pk=pk)
    
    # Clone the proposal
    proposal = Proposal()
    proposal.client = original.client
    proposal.title = f"Copy of {original.title}"
    proposal.description = original.description
    proposal.terms_and_conditions = original.terms_and_conditions
    proposal.issue_date = timezone.now().date()
    proposal.valid_until = original.valid_until
    proposal.tax_rate = original.tax_rate
    proposal.deposit_percentage = original.deposit_percentage
    proposal.notes = original.notes
    proposal.created_by = request.user
    proposal.save()
    
    # Clone line items
    for item in original.line_items.all():
        ProposalLineItem.objects.create(
            proposal=proposal,
            description=item.description,
            quantity=item.quantity,
            rate=item.rate,
            order=item.order
        )
    
    # Recalculate totals
    proposal.calculate_totals()
    
    messages.success(request, f'Proposal duplicated! New proposal number: {proposal.proposal_number}')
    return redirect('billing:edit_proposal', pk=proposal.pk)


@staff_member_required(login_url='/portal/login/')
def delete_proposal(request, pk):
    """Delete a proposal (staff only)."""
    from .models import Proposal
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    # Don't allow deleting accepted proposals
    if proposal.status == 'accepted':
        messages.error(request, 'Cannot delete accepted proposals.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if request.method == 'POST':
        proposal_number = proposal.proposal_number
        proposal.delete()
        messages.success(request, f'Proposal {proposal_number} deleted successfully!')
        return redirect('billing:proposal_list')
    
    context = {'proposal': proposal}
    return render(request, 'billing/proposal_confirm_delete.html', context)


@login_required(login_url='/portal/login/')
def accept_proposal(request, pk):
    """Accept a proposal (client only)."""
    from .models import Proposal
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    # Only clients can accept their own proposals
    if not hasattr(request.user, 'client_profile') or proposal.client != request.user.client_profile:
        messages.error(request, 'You do not have permission to accept this proposal.')
        return redirect('billing:dashboard')
    
    if proposal.status == 'accepted':
        messages.info(request, 'This proposal has already been accepted.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if proposal.is_expired:
        messages.error(request, 'This proposal has expired.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if request.method == 'POST':
        proposal.status = 'accepted'
        proposal.accepted_date = timezone.now()
        proposal.accepted_by = proposal.client.get_full_name()
        proposal.acceptance_ip = request.META.get('REMOTE_ADDR')
        proposal.save()
        
        messages.success(request, f'Proposal {proposal.proposal_number} accepted! We will begin work on your project soon.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    context = {'proposal': proposal}
    return render(request, 'billing/proposal_accept.html', context)


@login_required(login_url='/portal/login/')
def reject_proposal(request, pk):
    """Reject a proposal (client only)."""
    from .models import Proposal
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    # Only clients can reject their own proposals
    if not hasattr(request.user, 'client_profile') or proposal.client != request.user.client_profile:
        messages.error(request, 'You do not have permission to reject this proposal.')
        return redirect('billing:dashboard')
    
    if proposal.status in ['accepted', 'rejected']:
        messages.info(request, f'This proposal has already been {proposal.get_status_display().lower()}.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if request.method == 'POST':
        proposal.status = 'rejected'
        proposal.rejected_date = timezone.now()
        proposal.save()
        
        messages.info(request, f'Proposal {proposal.proposal_number} declined.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    context = {'proposal': proposal}
    return render(request, 'billing/proposal_reject.html', context)


@staff_member_required(login_url='/portal/login/')
def send_proposal(request, pk):
    """Send proposal email to client (staff only)."""
    from .models import Proposal
    from django.core.mail import EmailMultiAlternatives
    from django.urls import reverse
    
    proposal = get_object_or_404(Proposal, pk=pk)
    
    if proposal.status == 'accepted':
        messages.warning(request, 'This proposal has already been accepted.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if proposal.status == 'rejected':
        messages.warning(request, 'This proposal has already been rejected.')
        return redirect('billing:proposal_detail', pk=proposal.pk)
    
    if request.method == 'POST':
        try:
            # Build proposal URL
            proposal_url = request.build_absolute_uri(
                reverse('billing:proposal_detail', kwargs={'pk': proposal.pk})
            )
            
            # Prepare email context
            email_context = {
                'proposal': proposal,
                'client': proposal.client,
                'proposal_url': proposal_url,
                'company_name': settings.COMPANY_NAME,
                'contact_email': settings.CONTACT_EMAIL,
                'contact_phone': settings.CONTACT_PHONE,
            }
            
            # Render email templates
            subject = f'Proposal {proposal.proposal_number} - {proposal.title}'
            text_content = render_to_string('billing/emails/proposal_notification.txt', email_context)
            html_content = render_to_string('billing/emails/proposal_notification.html', email_context)
            
            # Create email
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[proposal.client.email],
                reply_to=[settings.CONTACT_EMAIL]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            # Update proposal status
            proposal.status = 'sent'
            proposal.sent_date = timezone.now()
            proposal.save()
            
            messages.success(request, f'Proposal {proposal.proposal_number} sent to {proposal.client.email}')
            return redirect('billing:proposal_detail', pk=proposal.pk)
            
        except Exception as e:
            logger.error(f"Failed to send proposal {proposal.proposal_number}: {str(e)}")
            messages.error(request, f'Failed to send proposal email: {str(e)}')
            return redirect('billing:proposal_detail', pk=proposal.pk)
    
    context = {'proposal': proposal}
    return render(request, 'billing/proposal_send_confirm.html', context)
