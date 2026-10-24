import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .native_invoice_models import NativeInvoice
from .native_invoices import balance
from .pm_views import staff_view, editable, error_text
from .services import EditConflict
from .test_payment_models import NativePaymentAttempt, RESERVED_STATES
from .test_payment_gateway import configuration, TestStripeGateway, GatewayUnavailable, safe_checkout_url
from .test_payments import start, refresh, create_or_resume, handle_event


class StartForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    method = forms.ChoiceField(choices=[('card','Credit / debit card'),('bank','ACH bank payment')], label='Test payment method')
    confirm = forms.BooleanField(label='Open Stripe TEST checkout for this remaining test balance. No real payment is due.')


def private(response):
    response['Cache-Control']='private, no-store'
    response['Referrer-Policy']='same-origin'
    response['X-Robots-Tag']='noindex, nofollow, noarchive'
    return response


def hosted_checkout(request, attempt):
    # A normal link preserves the site's strict form-action CSP. Browsers can
    # otherwise block a POST redirect to Stripe even when its address is valid.
    return private(render(request, 'workqueue/test_payment_handoff.html', {'attempt': attempt}))


@staff_view
@require_GET
def overview(request):
    q=request.GET.get('q','').strip()[:150]
    attempts=NativePaymentAttempt.objects.select_related('invoice')
    if q:
        attempts=attempts.filter(Q(invoice__number__icontains=q)|Q(invoice__snapshot__client_name__icontains=q)|Q(invoice__snapshot__project_name__icontains=q))
    return private(render(request,'workqueue/test_payments.html',{'config':configuration(),'q':q,
        'page':Paginator(attempts,25).get_page(request.GET.get('page')),
        'attention_count':NativePaymentAttempt.objects.filter(state__in=['review','creating','processing']).count()}))


@staff_view
@require_http_methods(['GET','POST'])
def checkout(request,invoice_id):
    editable(request)
    invoice=get_object_or_404(NativeInvoice,pk=invoice_id)
    active=invoice.payment_attempts.filter(state__in=RESERVED_STATES).first()
    if active:return redirect('workqueue:test_payment_detail',active.pk)
    form=StartForm(request.POST or None,initial={'token':uuid.uuid4(),'version':invoice.version,'method':'card'})
    status=200
    if request.method=='POST':
        status=400
        if form.is_valid():
            try:
                attempt=start(invoice_id=invoice.pk,actor=request.user,**{
                    key:form.cleaned_data[key] for key in ['version','method','token']})
            except (ValidationError,GatewayUnavailable,EditConflict) as error:
                # An uncertain attempt is preserved and reachable from the invoice.
                active=invoice.payment_attempts.filter(state__in=RESERVED_STATES).first()
                if active:
                    messages.warning(request,error_text(error) if isinstance(error,ValidationError) else str(error))
                    return redirect('workqueue:test_payment_detail',active.pk)
                form.add_error(None,error_text(error) if isinstance(error,ValidationError) else str(error))
                status=409 if isinstance(error,EditConflict) else 400
            else:
                if attempt.state=='open' and safe_checkout_url(attempt.checkout_url):
                    return hosted_checkout(request, attempt)
                return redirect('workqueue:test_payment_detail',attempt.pk)
    return private(render(request,'workqueue/test_payment_start.html',{'invoice':invoice,'amounts':balance(invoice),
        'form':form,'config':configuration()},status=status))


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,attempt_id):
    attempt=get_object_or_404(NativePaymentAttempt.objects.select_related('invoice','actor'),pk=attempt_id)
    error='';status=200
    if request.method=='POST':
        editable(request)
        action=request.POST.get('action')
        try:
            if action=='refresh':attempt=refresh(attempt)
            elif action=='retry':attempt=create_or_resume(attempt)
            elif action=='expire' and request.POST.get('confirm')=='yes':attempt=refresh(attempt,expire=True)
            elif action=='recover':attempt=refresh(attempt,recovery_session=request.POST.get('session_id','').strip())
            elif action=='continue' and attempt.state=='open' and safe_checkout_url(attempt.checkout_url):
                # Recheck Stripe before redirecting to a stored hosted page.
                attempt=refresh(attempt)
                if attempt.state=='open' and safe_checkout_url(attempt.checkout_url):
                    return hosted_checkout(request, attempt)
            else:raise ValidationError('Choose a payment action and confirm closing an unused checkout.')
        except (ValidationError,GatewayUnavailable) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=400
        else:
            messages.success(request,'Stripe test status checked. No real payment or refund was processed by this action.')
            return redirect('workqueue:test_payment_detail',attempt.pk)
    return private(render(request,'workqueue/test_payment_detail.html',{'attempt':attempt,'error':error,
        'config':configuration(),'reserved':attempt.state in RESERVED_STATES,'amounts':balance(attempt.invoice)},status=status))


@csrf_exempt  # Stripe supplies a verified raw-body signature, not a browser CSRF token.
@require_POST
def webhook(request):
    if not configuration()['ready']:return HttpResponse(status=503)
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0)>262144:return HttpResponse(status=413)
    except ValueError:return HttpResponse(status=400)
    if len(request.body)>262144:return HttpResponse(status=413)
    from django.conf import settings
    import stripe
    try:
        event=stripe.Webhook.construct_event(request.body,request.headers.get('Stripe-Signature',''),settings.NATIVE_STRIPE_TEST_WEBHOOK_SECRET)
        handle_event(event)
    except (ValueError,ValidationError,stripe.SignatureVerificationError):return HttpResponse(status=400)
    except GatewayUnavailable:return HttpResponse(status=503)
    return HttpResponse(status=200)
