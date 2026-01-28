@staff_member_required(login_url='/portal/login/')
def project_list(request):
    """Staff view to list all projects."""
    from .models import Project
    projects = Project.objects.all().select_related('client').order_by('-created_at')

    # Add active timer for floating timer widget (staff only)
    from timetracking.models import ActiveTimer
    active_timer = None
    if request.user.is_staff:
        try:
            active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=request.user)
        except ActiveTimer.DoesNotExist:
            active_timer = None

    context = {
        'projects': projects,
        'active_timer': active_timer,
    }
    return render(request, 'billing/project_list.html', context)
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
# --- Portal Email for Employee ---
from django.views.decorators.http import require_POST

@staff_member_required(login_url='/portal/login/')
@require_POST
def send_portal_email(request, pk):
    """Send portal email to an employee (staff only)."""
    employee = get_object_or_404(Employee, pk=pk)
    if not employee.email:
        messages.error(request, "Employee does not have an email address on file.")
        return redirect('billing:employee_detail', pk=employee.pk)
    # Compose email (customize as needed)
    subject = "Your Portal Access for Provost Home Design"
    message = f"Hello {employee.get_full_name()},\n\nYou have been granted access to the Provost Home Design portal. Please log in with your credentials. If you have not received your login information, please contact your administrator.\n\nPortal URL: {request.build_absolute_uri('/portal/login/')}\n\nThank you!"
    from_email = settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else None
    recipient_list = [employee.email]
    try:
        send_mail(subject, message, from_email, recipient_list, fail_silently=False)
        messages.success(request, f"Portal email sent to {employee.get_full_name()} ({employee.email})!")
    except Exception as e:
        messages.error(request, f"Failed to send portal email: {e}")
    return redirect('billing:employee_detail', pk=employee.pk)
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
from django.db import models
from django.db.models import Sum, Q, F, ExpressionWrapper, DecimalField
from decimal import Decimal
import json
import logging
from datetime import timedelta, date
from io import BytesIO
import zipfile

from core.utils import verify_recaptcha_v3
from .models import Client, Employee, Invoice, Payment, InvoiceTemplate, InvoiceLineItem, ProposalLineItem, Project, Proposal, ClientPlanFile, Expense, ExpenseCategory, IncomingWorkLog
from .forms import (
    ClientRegistrationForm, 
    ClientLoginForm, 
    ClientProfileForm, 
    ClientPasswordResetForm,
    InvoiceForm,
    InvoiceLineItemFormSet,
    ClientForm,
    EmployeeForm,
    ClientPlanFileForm,
    ExpenseForm,
    IncomingWorkLogForm
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
    # Staff see an operational dashboard instead of the client view
    if request.user.is_staff:
        context = _staff_dashboard_context()
        return render(request, 'billing/staff_dashboard.html', context)

    try:
        client = request.user.client_profile
    except Client.DoesNotExist:
        # Create client profile if doesn't exist
        client = Client.objects.create(user=request.user)
    
    # Get invoices
    invoices = client.invoices.all().order_by('-created_at')[:10]
    
    # Calculate totals
    total_outstanding = sum(inv.get_balance_due() for inv in client.invoices.exclude(status='paid'))
    total_paid = client.invoices.filter(status='paid').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
    
    # Recent payments
    recent_payments = Payment.objects.filter(
        invoice__client=client,
        status='succeeded'
    ).order_by('-processed_at')[:5].select_related('invoice')
    
    # Count invoices by status
    pending_count = client.invoices.filter(status__in=['sent', 'overdue']).count()
    
    # Recent proposals
    recent_proposals = Proposal.objects.filter(client=client).order_by('-created_at')[:5]
    
    # Recent plan files
    from .models import ClientPlanFile
    recent_plan_files = ClientPlanFile.objects.filter(client=client).order_by('-uploaded_at')[:5]
    
    # Incoming Work Log form
    if request.method == 'POST' and 'incoming_worklog_submit' in request.POST:
        worklog_form = IncomingWorkLogForm(request.POST, request.FILES)
        if worklog_form.is_valid():
            worklog = worklog_form.save(commit=False)
            worklog.created_by = request.user
            worklog.save()
            messages.success(request, 'Incoming work log added!')
            return redirect('billing:dashboard')
    else:
        worklog_form = IncomingWorkLogForm()

    recent_logs = IncomingWorkLog.objects.order_by('-created_at')[:10]
    context = {
        'client': client,
        'invoices': invoices,
        'total_outstanding': total_outstanding,
        'total_paid': total_paid,
        'recent_payments': recent_payments,
        'pending_count': pending_count,
        'recent_proposals': recent_proposals,
        'recent_plan_files': recent_plan_files,
        'now': timezone.now(),
        'worklog_form': worklog_form,
        'recent_logs': recent_logs,
    }
    
    return render(request, 'billing/dashboard.html', context)


def _staff_dashboard_context():
    """Build data for the staff-facing CRM dashboard."""
    today = timezone.now().date()
    now_dt = timezone.now()

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    invoices = Invoice.objects.select_related('client')
    active_invoices = invoices.exclude(status='cancelled')
    outstanding_qs = active_invoices.exclude(status='paid').annotate(balance=balance_expr)

    total_outstanding = outstanding_qs.aggregate(total=Sum('balance'))['total'] or Decimal('0.00')
    overdue_qs = outstanding_qs.filter(due_date__lt=today)
    overdue_total = overdue_qs.aggregate(total=Sum('balance'))['total'] or Decimal('0.00')
    current_total = total_outstanding - overdue_total

    # Buckets similar to FreshBooks aging
    aging_buckets = {
        'current': {'label': 'Current', 'total': Decimal('0.00'), 'count': 0},
        '0-30': {'label': '0-30 Days', 'total': Decimal('0.00'), 'count': 0},
        '31-60': {'label': '31-60 Days', 'total': Decimal('0.00'), 'count': 0},
        '61-90': {'label': '61-90 Days', 'total': Decimal('0.00'), 'count': 0},
        '91+': {'label': '91+ Days', 'total': Decimal('0.00'), 'count': 0},
    }

    for inv in outstanding_qs:
        balance = inv.balance or (inv.total - inv.amount_paid)
        if balance <= 0:
            continue
        if inv.due_date:
            days_overdue = (today - inv.due_date).days
        else:
            days_overdue = 0
        if days_overdue <= 0:
            bucket = 'current'
        elif days_overdue <= 30:
            bucket = '0-30'
        elif days_overdue <= 60:
            bucket = '31-60'
        elif days_overdue <= 90:
            bucket = '61-90'
        else:
            bucket = '91+'
        aging_buckets[bucket]['total'] += balance
        aging_buckets[bucket]['count'] += 1

    # Invoice status counts
    invoice_status_counts = {
        'draft': active_invoices.filter(status='draft').count(),
        'sent': active_invoices.filter(status='sent').count(),
        'paid': active_invoices.filter(status='paid').count(),
        'overdue': active_invoices.filter(status='overdue').count(),
    }

    # Payments in the last 30 days
    thirty_days_ago = now_dt - timedelta(days=30)
    payments_last_30 = Payment.objects.filter(
        status='succeeded',
        processed_at__isnull=False,
        processed_at__gte=thirty_days_ago,
    )
    paid_last_30 = payments_last_30.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Revenue trend for the last 6 months (based on successful payments)
    def _shift_month(dt: date, delta: int) -> date:
        month = dt.month + delta
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1)

    month_labels = []
    month_totals = []
    current_month_start = today.replace(day=1)
    for offset in range(5, -1, -1):
        month_start = _shift_month(current_month_start, -offset)
        next_month = _shift_month(month_start, 1)
        total = Payment.objects.filter(
            status='succeeded',
            processed_at__date__gte=month_start,
            processed_at__date__lt=next_month,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        month_labels.append(month_start.strftime('%b %Y'))
        month_totals.append(float(total))

    # Top clients by outstanding balance
    top_clients = outstanding_qs.values(
        'client__id', 'client__first_name', 'client__last_name', 'client__company_name'
    ).annotate(balance=Sum('balance')).order_by('-balance')[:5]

    # Top overdue clients (for action card)
    top_overdue = overdue_qs.values(
        'client__id', 'client__first_name', 'client__last_name', 'client__company_name'
    ).annotate(balance=Sum('balance')).order_by('-balance')[:3]

    # Projects and proposals snapshots
    projects_in_progress = Project.objects.exclude(status__in=['completed', 'cancelled']).select_related('client')
    
    # Project health: on-time, at-risk, overdue
    project_health = {
        'on_time': projects_in_progress.filter(due_date__gte=today).count(),
        'at_risk': projects_in_progress.filter(due_date__lt=today + timedelta(days=7), due_date__gte=today).count(),
        'overdue': projects_in_progress.filter(due_date__lt=today).count(),
    }
    
    # Proposal conversion metrics
    all_proposals = Proposal.objects.exclude(status='draft')
    proposals_sent = all_proposals.filter(status__in=['sent', 'viewed']).count()
    proposals_accepted = all_proposals.filter(status='accepted').count()
    proposal_conversion_rate = (
        (proposals_accepted / proposals_sent * 100) if proposals_sent > 0 else 0
    )
    
    # Payment collection efficiency (paid / invoiced in last 30 days)
    invoices_last_30 = active_invoices.filter(issue_date__gte=(today - timedelta(days=30))).aggregate(
        total=Sum('total')
    )['total'] or Decimal('0.00')
    collection_efficiency = (
        (float(paid_last_30) / float(invoices_last_30) * 100) if invoices_last_30 > 0 else 0
    )
    active_projects = projects_in_progress[:5]
    proposal_counts = {
        'draft': Proposal.objects.filter(status='draft').count(),
        'sent': Proposal.objects.filter(status__in=['sent', 'viewed']).count(),
        'accepted': Proposal.objects.filter(status='accepted').count(),
    }
    recent_proposals = Proposal.objects.select_related('client').order_by('-issue_date')[:5]

    recent_invoices = active_invoices.order_by('-issue_date')[:6]
    recent_payments = Payment.objects.filter(status='succeeded').select_related('invoice__client').order_by('-processed_at')[:6]

    return {
        'total_outstanding': total_outstanding,
        'overdue_total': overdue_total,
        'current_total': current_total,
        'paid_last_30': paid_last_30,
        'invoice_status_counts': invoice_status_counts,
        'aging_buckets': aging_buckets,
        'month_labels': month_labels,
        'month_totals': month_totals,
        'top_clients': top_clients,
        'top_overdue': top_overdue,
        'project_health': project_health,
        'proposal_conversion_rate': proposal_conversion_rate,
        'collection_efficiency': collection_efficiency,
        'active_projects': active_projects,
        'projects_in_progress_count': projects_in_progress.count(),
        'proposal_counts': proposal_counts,
        'recent_proposals': recent_proposals,
        'recent_invoices': recent_invoices,
        'recent_payments': recent_payments,
        'total_clients': Client.objects.count(),
        'total_projects': Project.objects.count(),
    }


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


@staff_member_required(login_url='/portal/login/')
def upload_plan_file(request):
    """Staff view to upload plan files for clients."""
    if request.method == 'POST':
        form = ClientPlanFileForm(request.POST, request.FILES)
        if form.is_valid():
            plan_file = form.save(commit=False)
            plan_file.uploaded_by = request.user
            plan_file.save()
            form.save_m2m()
            # Send email notification if requested
            if form.cleaned_data.get('send_email_notification'):
                _send_plan_file_email(plan_file, request.user)
            messages.success(request, f'Plan file "{plan_file.file_name}" uploaded successfully!')
            return redirect('billing:upload_plan_file')
    else:
        form = ClientPlanFileForm()
    
    # Get recent uploads for display
    recent_uploads = ClientPlanFile.objects.select_related(
        'client', 'project', 'uploaded_by'
    ).order_by('-uploaded_at')[:10]
    
    # Add active timer for floating timer widget (staff only)
    from timetracking.models import ActiveTimer
    active_timer = None
    if request.user.is_staff:
        try:
            active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=request.user)
        except ActiveTimer.DoesNotExist:
            active_timer = None

    context = {
        'form': form,
        'recent_uploads': recent_uploads,
        'active_timer': active_timer,
    }
    
    return render(request, 'billing/upload_plan_file.html', context)


def _send_plan_file_email(plan_file, uploaded_by):
    """Helper function to send plan file notification email."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.conf import settings
    
    client = plan_file.client
    
    # Get client email - try user email first, then client profile email
    to_email = None
    if client.user and client.user.email:
        to_email = client.user.email
    elif client.email:
        to_email = client.email
    
    if not to_email:
        return  # Can't send email without an address
    
    context = {
        'plan_file': plan_file,
        'client': client,
        'uploaded_by': uploaded_by,
        'portal_url': settings.PORTAL_URL if hasattr(settings, 'PORTAL_URL') else 'https://provosthomedesign.com/portal/',
    }
    
    html_content = render_to_string('billing/emails/plan_file_notification.html', context)
    text_content = render_to_string('billing/emails/plan_file_notification.txt', context)
    
    subject = f'New Plan File Available: {plan_file.file_name}'
    
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    msg.attach_alternative(html_content, 'text/html')
    msg.send(fail_silently=True)


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
    
    search = request.GET.get('search', '').strip()
    client_qs = Client.objects.all()
    if search:
        client_qs = client_qs.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search) |
            Q(company_name__icontains=search)
        )
    clients = client_qs.annotate(
        invoice_count=Count('invoices'),
        total_billed=Sum('invoices__total'),
        total_paid=Sum('invoices__amount_paid'),
        outstanding=Sum('invoices__total') - Sum('invoices__amount_paid')
    ).order_by('last_name', 'first_name')

    # Calculate overall totals
    totals = client_qs.aggregate(
        total_billed=Sum('invoices__total'),
        total_outstanding=Sum('invoices__total') - Sum('invoices__amount_paid')
    )

    context = {
        'clients': clients,
        'total_billed': totals['total_billed'] or Decimal('0.00'),
        'total_outstanding': totals['total_outstanding'] or Decimal('0.00'),
        'search': search,
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
    employees = Employee.objects.select_related('user').all().order_by('status', 'last_name', 'first_name')
    for emp in employees:
        emp.last_login = emp.user.last_login if emp.user else None
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
    employee = get_object_or_404(Employee.objects.select_related('user'), pk=pk)
    last_login = employee.user.last_login if employee.user else None
    context = {'employee': employee, 'last_login': last_login}
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
    
    from timetracking.models import TimeEntry
    from billing.models import Invoice

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

    # Annotate each project with hours_logged, unbilled_hours, unbilled_amount, last_invoice_date
    project_data = []
    for project in projects:
        # Hours logged: sum of all time entries (in hours)
        time_entries = TimeEntry.objects.filter(project=project)
        hours_logged = sum([te.get_duration_decimal() for te in time_entries])

        # Unbilled hours: sum of hours not invoiced
        unbilled_entries = time_entries.filter(models.Q(invoiced=False) | models.Q(invoice__isnull=True))
        unbilled_hours = sum([te.get_duration_decimal() for te in unbilled_entries])

        # Amount: flat rate or unbilled hours × hourly rate
        if project.billing_type == 'flat_rate' and project.fixed_price:
            amount = project.fixed_price
        elif project.billing_type == 'hourly' and project.hourly_rate:
            amount = unbilled_hours * float(project.hourly_rate)
        else:
            amount = 0

        # Last invoice: most recent invoice date
        last_invoice = project.invoices.exclude(status='cancelled').order_by('-issue_date').first()
        last_invoice_date = last_invoice.issue_date if last_invoice else None

        # Due date
        due_date = project.due_date

        project_data.append({
            'project': project,
            'hours_logged': hours_logged,
            'unbilled_hours': unbilled_hours,
            'amount': amount,
            'last_invoice_date': last_invoice_date,
            'due_date': due_date,
        })

    # Add active timer for floating timer widget (staff only)
    from timetracking.models import ActiveTimer
    active_timer = None
    if request.user.is_staff:
        try:
            active_timer = ActiveTimer.objects.select_related('time_entry__project').get(user=request.user)
        except ActiveTimer.DoesNotExist:
            active_timer = None

    context = {
        'projects_data': project_data,
        'search_query': search_query,
        'status_filter': status_filter,
        'billing_type_filter': billing_type_filter,
        'client_filter': client_filter,
        'status_choices': Project.STATUS_CHOICES,
        'billing_type_choices': Project.BILLING_TYPE_CHOICES,
        'active_timer': active_timer,
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
def close_project(request, pk):
    """Close a project only if fully paid; otherwise warn the user."""
    from .models import Project
    project = get_object_or_404(Project, pk=pk)

    payment_summary = project.get_payment_summary()

    if request.method == 'POST':
        if not payment_summary['is_fully_paid']:
            messages.error(
                request,
                'Project cannot be closed: outstanding balance remains (status: %s).' % payment_summary['status']
            )
            return redirect('billing:project_detail', pk=project.pk)

        project.is_closed = True
        project.closed_date = timezone.now()
        project.closed_by = request.user
        # Keep existing status, but if still in_progress bump to completed
        if project.status == 'in_progress':
            project.status = 'completed'
        project.save(update_fields=['is_closed', 'closed_date', 'closed_by', 'status', 'updated_at'])
        messages.success(request, 'Project closed. All invoices are paid in full.')
        return redirect('billing:project_detail', pk=project.pk)

    context = {
        'project': project,
        'payment_summary': payment_summary,
    }
    return render(request, 'billing/project_close_confirm.html', context)


@staff_member_required(login_url='/portal/login/')
def reopen_project(request, pk):
    """Reopen a closed project."""
    from .models import Project
    project = get_object_or_404(Project, pk=pk)

    if request.method == 'POST':
        project.is_closed = False
        project.closed_date = None
        project.closed_by = None
        # If previously completed, move back to in_progress for further work
        if project.status == 'completed':
            project.status = 'in_progress'
        project.save(update_fields=['is_closed', 'closed_date', 'closed_by', 'status', 'updated_at'])
        messages.success(request, 'Project reopened.')
        return redirect('billing:project_detail', pk=project.pk)

    context = {'project': project}
    return render(request, 'billing/project_reopen_confirm.html', context)


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


# ==================== PROPOSAL → INVOICE (Staff) ====================

@staff_member_required(login_url='/portal/login/')
def proposal_convert_to_invoice(request, pk):
    """Create a draft invoice from a proposal, copying line items and tax."""
    from .models import SystemSettings

    proposal = get_object_or_404(Proposal.objects.select_related('client', 'project'), pk=pk)

    if proposal.status == 'rejected':
        messages.error(request, 'Cannot convert a rejected proposal.')
        return redirect('billing:proposal_detail', pk=proposal.pk)

    # Optionally mark accepted when converting (keeps history consistent)
    if proposal.status not in ['accepted', 'rejected']:
        proposal.status = 'accepted'
        proposal.accepted_date = timezone.now()
        proposal.accepted_by = proposal.client.get_full_name()
        proposal.acceptance_ip = request.META.get('REMOTE_ADDR')
        proposal.save(update_fields=['status', 'accepted_date', 'accepted_by', 'acceptance_ip'])

    settings_obj = SystemSettings.load()
    terms_days = settings_obj.default_payment_terms_days if hasattr(settings_obj, 'default_payment_terms_days') else 30

    due_date = timezone.now().date() + timedelta(days=terms_days)

    # Create invoice shell
    invoice = Invoice.objects.create(
        client=proposal.client,
        project=proposal.project,
        status='draft',
        issue_date=timezone.now().date(),
        due_date=due_date,
        description=proposal.description or proposal.title,
        notes=proposal.terms_and_conditions,
        tax_rate=proposal.tax_rate,
    )

    # Copy line items
    line_items = []
    for item in proposal.line_items.all():
        total_val = (item.quantity * item.rate).quantize(Decimal('0.01'))
        line_items.append(InvoiceLineItem(
            invoice=invoice,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.rate,
            total=total_val,
            order=item.order,
        ))
    InvoiceLineItem.objects.bulk_create(line_items)

    # Recalculate totals
    invoice.calculate_totals()

    # Persist backlink for traceability
    proposal.linked_invoice = invoice
    proposal.save(update_fields=['linked_invoice'])

    messages.success(request, f'Invoice {invoice.invoice_number} created from proposal {proposal.proposal_number}.')
    return redirect('billing:invoice_detail', pk=invoice.pk)


# ==================== REPORTS (Staff Only) ====================

@staff_member_required(login_url='/portal/login/')
def reports_index(request):
    """Landing page listing available reports."""
    return render(request, 'billing/reports/index.html')


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_report(request):
    """Revenue by Client based on successful payments in a date range."""
    from datetime import datetime

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    # Default: last 6 months
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice__client')

    rows = payments.values(
        'invoice__client__id',
        'invoice__client__first_name',
        'invoice__client__last_name',
        'invoice__client__company_name',
    ).annotate(
        total_received=Sum('amount'),
        payments_count=models.Count('id'),
    ).order_by('-total_received')

    grand_total = rows.aggregate(t=Sum('total_received'))['t'] or Decimal('0.00')

    context = {
        'rows': rows,
        'grand_total': grand_total,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'billing/reports/revenue_by_client.html', context)


@staff_member_required(login_url='/portal/login/')
def accounts_aging_report(request):
    """Accounts aging buckets and per-client outstanding balances."""
    asof_str = request.GET.get('asof')
    today = timezone.now().date()
    if asof_str:
        try:
            from datetime import datetime
            today = datetime.strptime(asof_str, '%Y-%m-%d').date()
        except Exception:
            pass

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    outstanding = Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)

    # Per-client totals
    client_rows = outstanding.values(
        'client__id', 'client__first_name', 'client__last_name', 'client__company_name'
    ).annotate(
        outstanding_total=Sum('balance'),
        invoice_count=models.Count('id'),
    ).order_by('-outstanding_total')

    # Buckets
    buckets = {
        'current': {'label': 'Current', 'total': Decimal('0.00')},
        '0-30': {'label': '0-30 Days', 'total': Decimal('0.00')},
        '31-60': {'label': '31-60 Days', 'total': Decimal('0.00')},
        '61-90': {'label': '61-90 Days', 'total': Decimal('0.00')},
        '91+': {'label': '91+ Days', 'total': Decimal('0.00')},
    }

    for inv in outstanding:
        bal = inv.balance or (inv.total - inv.amount_paid)
        days = (today - inv.due_date).days if inv.due_date else 0
        if days <= 0:
            key = 'current'
        elif days <= 30:
            key = '0-30'
        elif days <= 60:
            key = '31-60'
        elif days <= 90:
            key = '61-90'
        else:
            key = '91+'
        buckets[key]['total'] += bal

    totals = {
        'outstanding_total': outstanding.aggregate(t=Sum('balance'))['t'] or Decimal('0.00'),
        'current_total': buckets['current']['total'],
        'overdue_total': buckets['0-30']['total'] + buckets['31-60']['total'] + buckets['61-90']['total'] + buckets['91+']['total'],
    }

    context = {
        'client_rows': client_rows,
        'buckets': buckets,
        'totals': totals,
        'today': today,
    }
    return render(request, 'billing/reports/accounts_aging.html', context)


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_pdf(request):
    """PDF export for Revenue by Client report."""
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice__client')

    rows = payments.values(
        'invoice__client__id',
        'invoice__client__first_name',
        'invoice__client__last_name',
        'invoice__client__company_name',
    ).annotate(
        total_received=Sum('amount'),
        payments_count=models.Count('id'),
    ).order_by('-total_received')

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph('<b>Revenue by Client</b>', styles['Heading1']))
    elements.append(Paragraph(f"Range: {start_date} to {end_date}", styles['Normal']))
    elements.append(Spacer(1, 0.3*inch))

    table_data = [['Client', 'Company', 'Payments', 'Total Received']]
    for r in rows:
        client_name = (f"{r['invoice__client__first_name']} {r['invoice__client__last_name']}").strip()
        company = r['invoice__client__company_name'] or ''
        table_data.append([
            client_name,
            company,
            str(r['payments_count'] or 0),
            f"${(r['total_received'] or Decimal('0.00')):,.2f}",
        ])

    total_val = rows.aggregate(t=Sum('total_received'))['t'] or Decimal('0.00')
    table_data.append(['', '', 'Grand Total', f"${total_val:,.2f}"])

    t = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 1*inch, 1.5*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="revenue_by_client_{start_date}_{end_date}.pdf"'
    return resp


@staff_member_required(login_url='/portal/login/')
def accounts_aging_pdf(request):
    """PDF export for Accounts Aging report."""
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    asof_str = request.GET.get('asof')
    today = timezone.now().date()
    if asof_str:
        try:
            today = datetime.strptime(asof_str, '%Y-%m-%d').date()
        except Exception:
            pass

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    outstanding = (
        Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)
    )

    buckets = {
        'current': Decimal('0.00'),
        '0-30': Decimal('0.00'),
        '31-60': Decimal('0.00'),
        '61-90': Decimal('0.00'),
        '91+': Decimal('0.00'),
    }
    for inv in outstanding:
        bal = inv.balance or (inv.total - inv.amount_paid)
        days = (today - inv.due_date).days if inv.due_date else 0
        if days <= 0:
            buckets['current'] += bal
        elif days <= 30:
            buckets['0-30'] += bal
        elif days <= 60:
            buckets['31-60'] += bal
        elif days <= 90:
            buckets['61-90'] += bal
        else:
            buckets['91+'] += bal

    client_rows = outstanding.values(
        'client__id', 'client__first_name', 'client__last_name', 'client__company_name'
    ).annotate(
        outstanding_total=Sum('balance'),
        invoice_count=models.Count('id'),
    ).order_by('-outstanding_total')

    totals_outstanding = outstanding.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    totals_current = buckets['current']
    totals_overdue = buckets['0-30'] + buckets['31-60'] + buckets['61-90'] + buckets['91+']

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph('<b>Accounts Aging</b>', styles['Heading1']))
    elements.append(Paragraph(f"As of {today}", styles['Normal']))
    elements.append(Spacer(1, 0.3*inch))

    totals_table = Table([
        ['Total Outstanding', f"${totals_outstanding:,.2f}"],
        ['Current', f"${totals_current:,.2f}"],
        ['Overdue', f"${totals_overdue:,.2f}"],
    ], colWidths=[3*inch, 2*inch])
    totals_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.3*inch))

    bucket_table = Table([
        ['Current', f"${buckets['current']:,.2f}"],
        ['0-30 Days', f"${buckets['0-30']:,.2f}"],
        ['31-60 Days', f"${buckets['31-60']:,.2f}"],
        ['61-90 Days', f"${buckets['61-90']:,.2f}"],
        ['91+ Days', f"${buckets['91+']:,.2f}"],
    ], colWidths=[3*inch, 2*inch])
    bucket_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(Paragraph('<b>Aging Buckets</b>', styles['Heading2']))
    elements.append(bucket_table)
    elements.append(Spacer(1, 0.3*inch))

    table_data = [['Client', 'Company', 'Invoices', 'Outstanding']]
    for r in client_rows:
        client_name = (f"{r['client__first_name']} {r['client__last_name']}").strip()
        company = r['client__company_name'] or ''
        table_data.append([
            client_name,
            company,
            str(r['invoice_count'] or 0),
            f"${(r['outstanding_total'] or Decimal('0.00')):,.2f}",
        ])
    t = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 1*inch, 1.5*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(Paragraph('<b>Per-Client Totals</b>', styles['Heading2']))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="accounts_aging_{today}.pdf"'
    return resp


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_csv(request):
    """CSV export for Revenue by Client report."""
    from datetime import datetime
    import csv

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice__client')

    rows = payments.values(
        'invoice__client__id',
        'invoice__client__first_name',
        'invoice__client__last_name',
        'invoice__client__company_name',
    ).annotate(
        total_received=Sum('amount'),
        payments_count=models.Count('id'),
    ).order_by('-total_received')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="revenue_by_client_{start_date}_{end_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Client', 'Company', 'Payments', 'Total Received'])
    for r in rows:
        client_name = f"{r['invoice__client__first_name']} {r['invoice__client__last_name']}".strip()
        company = r['invoice__client__company_name'] or ''
        writer.writerow([
            client_name,
            company,
            r['payments_count'] or 0,
            f"{(r['total_received'] or Decimal('0.00')):.2f}",
        ])
    return response


@staff_member_required(login_url='/portal/login/')
def accounts_aging_csv(request):
    """CSV export for Accounts Aging report (per-client totals)."""
    import csv

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    outstanding = Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)

    client_rows = outstanding.values(
        'client__first_name', 'client__last_name', 'client__company_name'
    ).annotate(
        outstanding_total=Sum('balance'),
        invoice_count=models.Count('id'),
    ).order_by('-outstanding_total')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="accounts_aging.csv"'
    writer = csv.writer(response)
    writer.writerow(['Client', 'Company', 'Invoices', 'Outstanding Total'])
    for r in client_rows:
        client_name = f"{r['client__first_name']} {r['client__last_name']}".strip()
        company = r['client__company_name'] or ''
        writer.writerow([
            client_name,
            company,
            r['invoice_count'] or 0,
            f"{(r['outstanding_total'] or Decimal('0.00')):.2f}",
        ])
    return response


@staff_member_required(login_url='/portal/login/')
def accounts_aging_bucket_detail(request, bucket: str):
    """Drill-down: list outstanding invoices in a specific aging bucket."""
    asof_str = request.GET.get('asof')
    today = timezone.now().date()
    if asof_str:
        try:
            from datetime import datetime
            today = datetime.strptime(asof_str, '%Y-%m-%d').date()
        except Exception:
            pass

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    outstanding = Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)

    def in_bucket(inv):
        days = (today - inv.due_date).days if inv.due_date else 0
        if bucket == 'current':
            return days <= 0
        if bucket == '0-30':
            return 1 <= days <= 30
        if bucket == '31-60':
            return 31 <= days <= 60
        if bucket == '61-90':
            return 61 <= days <= 90
        if bucket == '91+':
            return days >= 91
        return False

    invoices = [inv for inv in outstanding if in_bucket(inv)]
    total = sum((inv.balance or (inv.total - inv.amount_paid)) for inv in invoices) if invoices else Decimal('0.00')

    context = {
        'bucket': bucket,
        'invoices': invoices,
        'total': total,
        'today': today,
    }
    return render(request, 'billing/reports/accounts_aging_bucket_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_client_detail(request, client_id: int):
    """Drill-down: payments for a specific client within date range."""
    from datetime import datetime

    client = get_object_or_404(Client, pk=client_id)

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        invoice__client=client,
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice')

    total_received = payments.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    context = {
        'client': client,
        'payments': payments.order_by('-processed_at'),
        'total_received': total_received,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'billing/reports/revenue_by_client_client_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_client_csv(request, client_id: int):
    """CSV export for a client's payments within a date range."""
    from datetime import datetime
    import csv

    client = get_object_or_404(Client, pk=client_id)

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        invoice__client=client,
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="client_{client_id}_payments_{start_date}_{end_date}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(['Date', 'Invoice', 'Amount', 'Method'])
    for p in payments.order_by('-processed_at'):
        writer.writerow([
            (p.processed_at.date().isoformat() if p.processed_at else ''),
            p.invoice.invoice_number,
            f"{(p.amount or Decimal('0.00')):.2f}",
            p.get_payment_method_display(),
        ])
    return response


@staff_member_required(login_url='/portal/login/')
def accounts_aging_bucket_detail_csv(request, bucket: str):
    """CSV export for invoices in a specific aging bucket."""
    import csv

    asof_str = request.GET.get('asof')
    today = timezone.now().date()
    if asof_str:
        try:
            from datetime import datetime
            today = datetime.strptime(asof_str, '%Y-%m-%d').date()
        except Exception:
            pass
    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    outstanding = (
        Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)
    )

    def in_bucket(inv):
        days = (today - inv.due_date).days if inv.due_date else 0
        if bucket == 'current':
            return days <= 0
        if bucket == '0-30':
            return 1 <= days <= 30
        if bucket == '31-60':
            return 31 <= days <= 60
        if bucket == '61-90':
            return 61 <= days <= 90
        if bucket == '91+':
            return days >= 91
        return False

    invoices = [inv for inv in outstanding if in_bucket(inv)]

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="aging_bucket_{bucket}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Invoice', 'Client', 'Due Date', 'Balance', 'Status'])
    for inv in invoices:
        writer.writerow([
            inv.invoice_number,
            inv.client.company_name or inv.client.get_full_name(),
            inv.due_date.isoformat() if inv.due_date else '',
            f"{(inv.balance or (inv.total - inv.amount_paid)):.2f}",
            inv.get_status_display(),
        ])
    return response


@staff_member_required(login_url='/portal/login/')
def accounts_aging_client_detail(request, client_id: int):
    """Per-client aging detail: list outstanding invoices for a client."""
    client = get_object_or_404(Client, pk=client_id)
    today = timezone.now().date()

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    invoices = (
        Invoice.objects.filter(client=client)
        .exclude(status='paid')
        .annotate(balance=balance_expr)
        .filter(balance__gt=0)
        .order_by('due_date')
    )

    total = invoices.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')

    context = {
        'client': client,
        'invoices': invoices,
        'total': total,
        'today': today,
    }
    return render(request, 'billing/reports/accounts_aging_client_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def accounts_aging_client_detail_csv(request, client_id: int):
    """CSV export for per-client aging detail (outstanding invoices)."""
    import csv
    client = get_object_or_404(Client, pk=client_id)

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    invoices = (
        Invoice.objects.filter(client=client)
        .exclude(status='paid')
        .annotate(balance=balance_expr)
        .filter(balance__gt=0)
        .order_by('due_date')
    )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="aging_client_{client_id}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Invoice', 'Due Date', 'Balance', 'Status'])
    for inv in invoices:
        writer.writerow([
            inv.invoice_number,
            inv.due_date.isoformat() if inv.due_date else '',
            f"{(inv.balance or (inv.total - inv.amount_paid)):.2f}",
            inv.get_status_display(),
        ])
    return response


@staff_member_required(login_url='/portal/login/')
def revenue_by_client_client_pdf(request, client_id: int):
    """PDF export for a client's payments within a date range."""
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    client = get_object_or_404(Client, pk=client_id)

    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    today = timezone.now().date()
    default_start = (today.replace(day=1) - timedelta(days=180))
    default_end = today

    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback

    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)

    payments = Payment.objects.filter(
        status='succeeded',
        invoice__client=client,
        processed_at__date__gte=start_date,
        processed_at__date__lte=end_date,
    ).select_related('invoice')

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"<b>Payments for {client.company_name or client.get_full_name()}</b>", styles['Heading1']))
    elements.append(Paragraph(f"Range: {start_date} to {end_date}", styles['Normal']))
    elements.append(Spacer(1, 0.3*inch))

    table_data = [['Date', 'Invoice', 'Amount', 'Method']]
    total_val = Decimal('0.00')
    for p in payments.order_by('-processed_at'):
        total_val += p.amount or Decimal('0.00')
        table_data.append([
            (p.processed_at.date().isoformat() if p.processed_at else ''),
            p.invoice.invoice_number,
            f"${(p.amount or Decimal('0.00')):,.2f}",
            p.get_payment_method_display(),
        ])
    table_data.append(['', 'Total Received', f"${total_val:,.2f}", ''])

    t = Table(table_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = (
        f'attachment; filename="client_{client_id}_payments_{start_date}_{end_date}.pdf"'
    )
    return resp


@staff_member_required(login_url='/portal/login/')
def accounts_aging_client_detail_pdf(request, client_id: int):
    """PDF export for outstanding invoices for a client."""
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    client = get_object_or_404(Client, pk=client_id)

    balance_expr = ExpressionWrapper(
        F('total') - F('amount_paid'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    invoices = (
        Invoice.objects.filter(client=client)
        .exclude(status='paid')
        .annotate(balance=balance_expr)
        .filter(balance__gt=0)
        .order_by('due_date')
    )

    total_val = invoices.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"<b>Outstanding for {client.company_name or client.get_full_name()}</b>", styles['Heading1']))
    elements.append(Spacer(1, 0.3*inch))

    table_data = [['Invoice', 'Due Date', 'Balance', 'Status']]
    for inv in invoices:
        table_data.append([
            inv.invoice_number,
            inv.due_date.isoformat() if inv.due_date else '',
            f"{(inv.balance or (inv.total - inv.amount_paid)):.2f}",
            inv.get_status_display(),
        ])
    table_data.append(['', 'Total Outstanding', f"${total_val:,.2f}", ''])

    t = Table(table_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 2*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="aging_client_{client_id}.pdf"'
    return resp


@staff_member_required(login_url='/portal/login/')
def export_reports_bundle(request):
    """Generate a ZIP containing CSV and PDF exports for revenue-by-client and accounts aging.
    Optional drill-downs via `include_drilldowns=1`.
    Filters:
    - Revenue: `start`, `end`
    - Aging: `asof`
    """
    from datetime import datetime
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    import csv

    include_drilldowns = request.GET.get('include_drilldowns') in ('1', 'true', 'yes')

    # Parse filters
    today = timezone.now().date()
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')
    asof_str = request.GET.get('asof')
    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return fallback
    rev_start = parse_date(start_date, (today.replace(day=1) - timedelta(days=180)))
    rev_end = parse_date(end_date, today)
    asof_date = parse_date(asof_str, today)

    # Prepare in-memory zip
    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Revenue by Client data
        payments = Payment.objects.filter(
            status='succeeded',
            processed_at__date__gte=rev_start,
            processed_at__date__lte=rev_end,
        ).select_related('invoice__client')

        rev_rows = payments.values(
            'invoice__client__id',
            'invoice__client__first_name',
            'invoice__client__last_name',
            'invoice__client__company_name',
        ).annotate(
            total_received=Sum('amount'),
            payments_count=models.Count('id'),
        ).order_by('-total_received')

        # Revenue CSV
        csv_buf = BytesIO()
        writer = csv.writer(csv_buf)
        writer.writerow(['Client', 'Company', 'Payments', 'Total Received'])
        for r in rev_rows:
            client_name = (f"{r['invoice__client__first_name']} {r['invoice__client__last_name']}").strip()
            company = r['invoice__client__company_name'] or ''
            writer.writerow([
                client_name,
                company,
                r['payments_count'] or 0,
                f"{(r['total_received'] or Decimal('0.00')):.2f}",
            ])
        zf.writestr(f"revenue_by_client_{rev_start}_{rev_end}.csv", csv_buf.getvalue().decode('utf-8'))

        # Revenue PDF
        pdf_buf = BytesIO()
        doc = SimpleDocTemplate(pdf_buf, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph('<b>Revenue by Client</b>', styles['Heading1']))
        elements.append(Paragraph(f"Range: {rev_start} to {rev_end}", styles['Normal']))
        elements.append(Spacer(1, 0.3*inch))
        table_data = [['Client', 'Company', 'Payments', 'Total Received']]
        for r in rev_rows:
            client_name = (f"{r['invoice__client__first_name']} {r['invoice__client__last_name']}").strip()
            company = r['invoice__client__company_name'] or ''
            table_data.append([
                client_name,
                company,
                str(r['payments_count'] or 0),
                f"${(r['total_received'] or Decimal('0.00')):,.2f}",
            ])
        total_val = rev_rows.aggregate(t=Sum('total_received'))['t'] or Decimal('0.00')
        table_data.append(['', '', 'Grand Total', f"${total_val:,.2f}"])
        t = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 1*inch, 1.5*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(t)
        doc.build(elements)
        zf.writestr(f"revenue_by_client_{rev_start}_{rev_end}.pdf", pdf_buf.getvalue())

        if include_drilldowns:
            # Per-client payments CSVs and PDFs
            for r in rev_rows:
                client_id = r['invoice__client__id']
                client_name = (f"{r['invoice__client__first_name']} {r['invoice__client__last_name']}").strip()
                client_company = r['invoice__client__company_name'] or ''
                c_payments = Payment.objects.filter(
                    status='succeeded',
                    invoice__client_id=client_id,
                    processed_at__date__gte=rev_start,
                    processed_at__date__lte=rev_end,
                ).select_related('invoice')

                # CSV
                c_csv = BytesIO()
                w = csv.writer(c_csv)
                w.writerow(['Date', 'Invoice', 'Amount', 'Method'])
                total = Decimal('0.00')
                for p in c_payments.order_by('-processed_at'):
                    total += p.amount or Decimal('0.00')
                    w.writerow([
                        (p.processed_at.date().isoformat() if p.processed_at else ''),
                        p.invoice.invoice_number,
                        f"{(p.amount or Decimal('0.00')):.2f}",
                        p.get_payment_method_display(),
                    ])
                w.writerow(['', 'Total Received', f"{total:.2f}", ''])
                zf.writestr(f"revenue_by_client/clients/{client_id}_payments_{rev_start}_{rev_end}.csv", c_csv.getvalue().decode('utf-8'))

                # PDF
                c_pdf = BytesIO()
                doc = SimpleDocTemplate(c_pdf, pagesize=letter)
                elements = []
                elements.append(Paragraph(f"<b>Payments for {client_company or client_name}</b>", styles['Heading1']))
                elements.append(Paragraph(f"Range: {rev_start} to {rev_end}", styles['Normal']))
                elements.append(Spacer(1, 0.3*inch))
                table_data = [['Date', 'Invoice', 'Amount', 'Method']]
                for p in c_payments.order_by('-processed_at'):
                    table_data.append([
                        (p.processed_at.date().isoformat() if p.processed_at else ''),
                        p.invoice.invoice_number,
                        f"${(p.amount or Decimal('0.00')):,.2f}",
                        p.get_payment_method_display(),
                    ])
                table_data.append(['', 'Total Received', f"${total:,.2f}", ''])
                t = Table(table_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ]))
                elements.append(t)
                doc.build(elements)
                zf.writestr(f"revenue_by_client/clients/{client_id}_payments_{rev_start}_{rev_end}.pdf", c_pdf.getvalue())

        # Accounts Aging data
        balance_expr = ExpressionWrapper(
            F('total') - F('amount_paid'),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        outstanding = Invoice.objects.exclude(status='paid').annotate(balance=balance_expr).filter(balance__gt=0)

        buckets = {
            'current': Decimal('0.00'),
            '0-30': Decimal('0.00'),
            '31-60': Decimal('0.00'),
            '61-90': Decimal('0.00'),
            '91+': Decimal('0.00'),
        }
        for inv in outstanding:
            bal = inv.balance or (inv.total - inv.amount_paid)
            days = (asof_date - inv.due_date).days if inv.due_date else 0
            if days <= 0:
                buckets['current'] += bal
            elif days <= 30:
                buckets['0-30'] += bal
            elif days <= 60:
                buckets['31-60'] += bal
            elif days <= 90:
                buckets['61-90'] += bal
            else:
                buckets['91+'] += bal

        client_rows = outstanding.values(
            'client__id', 'client__first_name', 'client__last_name', 'client__company_name'
        ).annotate(
            outstanding_total=Sum('balance'),
            invoice_count=models.Count('id'),
        ).order_by('-outstanding_total')

        # Aging CSV
        a_csv = BytesIO()
        w = csv.writer(a_csv)
        w.writerow(['Client', 'Company', 'Invoices', 'Outstanding'])
        for r in client_rows:
            name = (f"{r['client__first_name']} {r['client__last_name']}").strip()
            company = r['client__company_name'] or ''
            w.writerow([
                name,
                company,
                r['invoice_count'] or 0,
                f"{(r['outstanding_total'] or Decimal('0.00')):.2f}",
            ])
        zf.writestr(f"accounts_aging_{asof_date}.csv", a_csv.getvalue().decode('utf-8'))

        # Aging PDF
        a_pdf = BytesIO()
        doc = SimpleDocTemplate(a_pdf, pagesize=letter)
        elements = []
        elements.append(Paragraph('<b>Accounts Aging</b>', styles['Heading1']))
        elements.append(Paragraph(f"As of {asof_date}", styles['Normal']))
        elements.append(Spacer(1, 0.3*inch))
        totals_outstanding = outstanding.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
        totals_current = buckets['current']
        totals_overdue = buckets['0-30'] + buckets['31-60'] + buckets['61-90'] + buckets['91+']
        totals_table = Table([
            ['Total Outstanding', f"${totals_outstanding:,.2f}"],
            ['Current', f"${totals_current:,.2f}"],
            ['Overdue', f"${totals_overdue:,.2f}"],
        ], colWidths=[3*inch, 2*inch])
        totals_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.black)]))
        elements.append(totals_table)
        elements.append(Spacer(1, 0.3*inch))
        bucket_table = Table([
            ['Current', f"${buckets['current']:,.2f}"],
            ['0-30 Days', f"${buckets['0-30']:,.2f}"],
            ['31-60 Days', f"${buckets['31-60']:,.2f}"],
            ['61-90 Days', f"${buckets['61-90']:,.2f}"],
            ['91+ Days', f"${buckets['91+']:,.2f}"],
        ], colWidths=[3*inch, 2*inch])
        bucket_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.black)]))
        elements.append(Paragraph('<b>Aging Buckets</b>', styles['Heading2']))
        elements.append(bucket_table)
        elements.append(Spacer(1, 0.3*inch))
        table_data = [['Client', 'Company', 'Invoices', 'Outstanding']]
        for r in client_rows:
            name = (f"{r['client__first_name']} {r['client__last_name']}").strip()
            company = r['client__company_name'] or ''
            table_data.append([
                name,
                company,
                str(r['invoice_count'] or 0),
                f"${(r['outstanding_total'] or Decimal('0.00')):,.2f}",
            ])
        t = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 1*inch, 1.5*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        elements.append(Paragraph('<b>Per-Client Totals</b>', styles['Heading2']))
        elements.append(t)
        doc.build(elements)
        zf.writestr(f"accounts_aging_{asof_date}.pdf", a_pdf.getvalue())

        if include_drilldowns:
            # Per-bucket CSVs
            for bucket in ['current', '0-30', '31-60', '61-90', '91+']:
                def in_bucket(inv):
                    days = (asof_date - inv.due_date).days if inv.due_date else 0
                    if bucket == 'current':
                        return days <= 0
                    if bucket == '0-30':
                        return 1 <= days <= 30
                    if bucket == '31-60':
                        return 31 <= days <= 60
                    if bucket == '61-90':
                        return 61 <= days <= 90
                    if bucket == '91+':
                        return days >= 91
                    return False
                invs = [inv for inv in outstanding if in_bucket(inv)]
                b_csv = BytesIO()
                w = csv.writer(b_csv)
                w.writerow(['Invoice', 'Client', 'Due Date', 'Balance', 'Status'])
                for inv in invs:
                    w.writerow([
                        inv.invoice_number,
                        inv.client.company_name or inv.client.get_full_name(),
                        inv.due_date.isoformat() if inv.due_date else '',
                        f"{(inv.balance or (inv.total - inv.amount_paid)):.2f}",
                        inv.get_status_display(),
                    ])
                zf.writestr(f"accounts_aging/buckets/{bucket}_{asof_date}.csv", b_csv.getvalue().decode('utf-8'))

            # Per-client aging CSVs and PDFs
            for r in client_rows:
                client_id = r['client__id']
                client_name = (f"{r['client__first_name']} {r['client__last_name']}").strip()
                client_company = r['client__company_name'] or ''
                invs = (
                    Invoice.objects.filter(client_id=client_id)
                    .exclude(status='paid')
                    .annotate(balance=balance_expr)
                    .filter(balance__gt=0)
                    .order_by('due_date')
                )
                # CSV
                c_csv = BytesIO()
                w = csv.writer(c_csv)
                w.writerow(['Invoice', 'Due Date', 'Balance', 'Status'])
                total = Decimal('0.00')
                for inv in invs:
                    total += inv.balance or (inv.total - inv.amount_paid)
                    w.writerow([
                        inv.invoice_number,
                        inv.due_date.isoformat() if inv.due_date else '',
                        f"{(inv.balance or (inv.total - inv.amount_paid)):.2f}",
                        inv.get_status_display(),
                    ])
                w.writerow(['', 'Total Outstanding', f"{total:.2f}", ''])
                zf.writestr(f"accounts_aging/clients/{client_id}_aging_{asof_date}.csv", c_csv.getvalue().decode('utf-8'))
                # PDF
                c_pdf = BytesIO()
                doc = SimpleDocTemplate(c_pdf, pagesize=letter)
                elements = []
                elements.append(Paragraph(f"<b>Outstanding for {client_company or client_name}</b>", styles['Heading1']))
                elements.append(Paragraph(f"As of {asof_date}", styles['Normal']))
                elements.append(Spacer(1, 0.3*inch))
                table_data = [['Invoice', 'Due Date', 'Balance', 'Status']]
                for inv in invs:
                    table_data.append([
                        inv.invoice_number,
                        inv.due_date.isoformat() if inv.due_date else '',
                        f"${(inv.balance or (inv.total - inv.amount_paid)):.2f}",
                        inv.get_status_display(),
                    ])
                table_data.append(['', 'Total Outstanding', f"${total:,.2f}", ''])
                t = Table(table_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 2*inch])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ]))
                elements.append(t)
                doc.build(elements)
                zf.writestr(f"accounts_aging/clients/{client_id}_aging_{asof_date}.pdf", c_pdf.getvalue())

    # Return zip
    zip_buf.seek(0)
    resp = HttpResponse(zip_buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = (
        f'attachment; filename="reports_bundle_{rev_start}_{rev_end}_{asof_date}.zip"'
    )
    return resp

# ==================== EXPENSES (Staff Only) ====================

@staff_member_required(login_url='/portal/login/')
def expense_list(request):
    """List all expenses with filtering by status, category, date range."""
    expenses = Expense.objects.select_related('category', 'project', 'client', 'created_by').order_by('-expense_date')
    
    # Filters
    status = request.GET.get('status')
    category = request.GET.get('category')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if status:
        expenses = expenses.filter(status=status)
    if category:
        expenses = expenses.filter(category_id=category)
    if start_date:
        try:
            start = timezone.datetime.strptime(start_date, '%Y-%m-%d').date()
            expenses = expenses.filter(expense_date__gte=start)
        except:
            pass
    if end_date:
        try:
            end = timezone.datetime.strptime(end_date, '%Y-%m-%d').date()
            expenses = expenses.filter(expense_date__lte=end)
        except:
            pass
    
    # Calculations
    total_amount = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    approved_amount = expenses.filter(status='approved').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    pending_amount = expenses.filter(status='pending').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    categories = ExpenseCategory.objects.filter(is_active=True)
    
    context = {
        'expenses': expenses[:50],  # Pagination can be added later
        'categories': categories,
        'total_amount': total_amount,
        'approved_amount': approved_amount,
        'pending_amount': pending_amount,
        'status_filter': status,
        'category_filter': category,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'billing/expenses/list.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_detail(request, pk):
    """View expense details."""
    expense = get_object_or_404(Expense, pk=pk)
    
    context = {
        'expense': expense,
    }
    return render(request, 'billing/expenses/detail.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_create(request):
    """Create a new expense."""
    from .forms import ExpenseForm
    
    if request.method == 'POST':
        form = ExpenseForm(request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.created_by = request.user
            expense.save()
            messages.success(request, f'Expense "{expense.description}" created successfully.')
            return redirect('billing:expense_detail', pk=expense.pk)
    else:
        form = ExpenseForm()
    
    context = {
        'form': form,
        'title': 'Create Expense',
    }
    return render(request, 'billing/expenses/form.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_edit(request, pk):
    """Edit an expense."""
    from .forms import ExpenseForm
    
    expense = get_object_or_404(Expense, pk=pk)
    
    if request.method == 'POST':
        form = ExpenseForm(request.POST, instance=expense)
        if form.is_valid():
            expense = form.save()
            messages.success(request, 'Expense updated successfully.')
            return redirect('billing:expense_detail', pk=expense.pk)
    else:
        form = ExpenseForm(instance=expense)
    
    context = {
        'form': form,
        'expense': expense,
        'title': f'Edit Expense: {expense.description}',
    }
    return render(request, 'billing/expenses/form.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_delete(request, pk):
    """Delete an expense."""
    expense = get_object_or_404(Expense, pk=pk)
    
    if request.method == 'POST':
        desc = expense.description
        expense.delete()
        messages.success(request, f'Expense "{desc}" deleted.')
        return redirect('billing:expense_list')
    
    context = {
        'expense': expense,
    }
    return render(request, 'billing/expenses/delete.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_approve(request, pk):
    """Approve an expense."""
    expense = get_object_or_404(Expense, pk=pk)
    
    if request.method == 'POST':
        expense.approve(request.user)
        messages.success(request, f'Expense "{expense.description}" approved.')
        return redirect('billing:expense_detail', pk=expense.pk)
    
    context = {
        'expense': expense,
    }
    return render(request, 'billing/expenses/approve.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_report(request):
    """Expense report by category, project, or date range."""
    from datetime import datetime
    
    start_str = request.GET.get('start_date')
    end_str = request.GET.get('end_date')
    group_by = request.GET.get('group_by', 'category')  # category, project, month
    
    today = timezone.now().date()
    default_start = today.replace(day=1)
    default_end = today
    
    def parse_date(s, fallback):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except:
            return fallback
    
    start_date = parse_date(start_str, default_start)
    end_date = parse_date(end_str, default_end)
    
    expenses = Expense.objects.filter(
        expense_date__gte=start_date,
        expense_date__lte=end_date,
    ).select_related('category', 'project')
    
    if group_by == 'category':
        rows = expenses.values('category__name').annotate(
            total=Sum('amount'),
            count=models.Count('id'),
            tax_deductible=Sum('amount', filter=models.Q(tax_deductible=True)),
        ).order_by('-total')
    elif group_by == 'project':
        rows = expenses.values('project__job_name').annotate(
            total=Sum('amount'),
            count=models.Count('id'),
        ).order_by('-total')
    else:  # month
        rows = expenses.values(
            month_key=models.functions.TruncMonth('expense_date')
        ).annotate(
            total=Sum('amount'),
            count=models.Count('id'),
        ).order_by('-month_key')
    
    grand_total = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    context = {
        'rows': rows,
        'grand_total': grand_total,
        'start_date': start_date,
        'end_date': end_date,
        'group_by': group_by,
        'total_count': expenses.count(),
    }
    return render(request, 'billing/expenses/report.html', context)


@staff_member_required(login_url='/portal/login/')
def expense_dashboard(request):
    """Dashboard for expense overview and analytics."""
    today = timezone.now().date()
    month_start = today.replace(day=1)
    
    # Get all expenses for calculations
    all_expenses = Expense.objects.select_related('category')
    
    # Get totals
    total_amount = all_expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    pending_count = all_expenses.filter(status='pending').count()
    pending_amount = all_expenses.filter(status='pending').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    month_amount = all_expenses.filter(
        expense_date__gte=month_start,
        expense_date__lte=today
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Category breakdown
    category_totals = []
    for category in ExpenseCategory.objects.filter(is_active=True):
        expenses = all_expenses.filter(category=category)
        if expenses.exists():
            total = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            count = expenses.count()
            category_totals.append((category, total, count))
    
    # Monthly breakdown (last 6 months)
    monthly_breakdown = []
    from django.db.models.functions import TruncMonth
    for item in all_expenses.annotate(month=TruncMonth('expense_date')).values('month').annotate(
        total=Sum('amount'),
        count=models.Count('id')
    ).order_by('-month')[:6]:
        monthly_breakdown.append((item['month'], item['total'] or Decimal('0.00'), item['count']))
    
    # Tax deductible
    tax_deductible_total = all_expenses.filter(tax_deductible=True).aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')
    non_deductible_total = all_expenses.filter(tax_deductible=False).aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')
    
    context = {
        'pending_count': pending_count,
        'pending_amount': pending_amount,
        'total_amount': total_amount,
        'month_amount': month_amount,
        'category_totals': category_totals,
        'monthly_breakdown': monthly_breakdown,
        'tax_deductible_total': tax_deductible_total,
        'non_deductible_total': non_deductible_total,
    }
    return render(request, 'billing/expenses/report.html', context)

from .forms import IncomingWorkLogForm
from .models import IncomingWorkLog

@login_required
def incoming_work_log(request):
    if request.method == 'POST':
        form = IncomingWorkLogForm(request.POST, request.FILES)
        if form.is_valid():
            log = form.save(commit=False)
            log.created_by = request.user
            log.save()
            messages.success(request, 'Incoming work log added!')
            return redirect('billing:dashboard')
    else:
        form = IncomingWorkLogForm()
    # Show last 10 logs
    recent_logs = IncomingWorkLog.objects.order_by('-created_at')[:10]
    return render(request, 'billing/incoming_work_log_form.html', {'form': form, 'recent_logs': recent_logs})
