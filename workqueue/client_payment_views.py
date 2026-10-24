import hashlib
import uuid
from functools import wraps
from django import forms
from django.core.exceptions import ValidationError,PermissionDenied
from django.http import HttpResponse,JsonResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_http_methods,require_GET
from django_ratelimit.decorators import ratelimit
from .views import queue_enabled
from .pm_views import private_response,staff_view,error_text
from .invoice_delivery import session_delivery
from .native_invoices import balance
from .services import EditConflict
from .test_payment_models import NativePaymentAttempt
from .test_payment_gateway import configuration,GatewayUnavailable,safe_checkout_url
from .client_payment_models import NativeTestPaymentReceipt
from .client_payments import start_for_delivery,change_attempt,receipt_projection


class CheckoutForm(forms.Form):
    token=forms.UUIDField(widget=forms.HiddenInput)
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    method=forms.ChoiceField(choices=[('card','Credit / debit card'),('bank','ACH bank payment')],label='Test payment method')
    confirm=forms.BooleanField(label='I understand this is Stripe sandbox checkout. I will use fictional test details. No real payment is due.')


def scope(request,delivery_id):
    delivery=session_delivery(request,delivery_id)
    if not delivery or not delivery.allow_test_checkout:raise PermissionDenied
    return delivery


def attempt_for(delivery,attempt_id):
    return get_object_or_404(NativePaymentAttempt,pk=attempt_id,delivery=delivery,invoice=delivery.invoice)


def private_render(request,template,context,*,status=200,form=False):
    return private_response(render(request,template,context,status=status),has_form=form)


def private_access_errors(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        try:return view(request,*args,**kwargs)
        except PermissionDenied:
            return private_render(request,'workqueue/invoice_test_access.html',
                {'error':'Open the complete private link from your invoice test email. It may have expired or been revoked.'},status=403,form=True)
        except Http404:
            return private_response(HttpResponse('This private test record is unavailable.',status=404))
    return wrapped


def fingerprint(attempt):
    receipt=NativeTestPaymentReceipt.objects.filter(attempt=attempt).values_list('number',flat=True).first() or ''
    amounts=balance(attempt.invoice)
    return hashlib.sha256('|'.join([attempt.state,attempt.issue_code,receipt,str(amounts['paid']),str(amounts['credited']),str(amounts['due'])]).encode()).hexdigest()


def handoff(request,delivery,attempt):
    if not safe_checkout_url(attempt.checkout_url):raise ValidationError('This test checkout address is unavailable. Refresh payment status.')
    return private_render(request,'workqueue/client_test_payment_handoff.html',{'delivery':delivery,'attempt':attempt})


@queue_enabled
@require_http_methods(['GET','POST'])
@ratelimit(key='ip',rate='20/h',method='POST',block=False)
@private_access_errors
def start(request,delivery_id):
    delivery=scope(request,delivery_id)
    form=CheckoutForm(request.POST or None,initial={'token':uuid.uuid4(),'version':delivery.invoice.version,'method':'card'})
    error='';status=200;cfg=configuration()
    if request.method=='POST':
        status=400
        if getattr(request,'limited',False):error='Too many attempts. Please wait and try again.';status=429
        elif form.is_valid():
            try:attempt=start_for_delivery(delivery_id=delivery.pk,**{k:form.cleaned_data[k] for k in ['version','method','token']})
            except (ValidationError,GatewayUnavailable,EditConflict) as exc:
                active=delivery.payment_attempts.filter(state__in=['creating','open','processing','review']).first()
                if active:return redirect('workqueue:invoice_test_payment',delivery.pk,active.pk)
                error=error_text(exc) if isinstance(exc,ValidationError) else str(exc)
                status=409 if isinstance(exc,EditConflict) else 400
            else:
                if attempt.state=='open':return handoff(request,delivery,attempt)
                return redirect('workqueue:invoice_test_payment',delivery.pk,attempt.pk)
    return private_render(request,'workqueue/client_test_checkout.html',{'delivery':delivery,'form':form,'error':error,
        'config':cfg,'amounts':balance(delivery.invoice)},status=status,form=True)


@queue_enabled
@require_http_methods(['GET','POST'])
@ratelimit(key='ip',rate='30/h',method='POST',block=False)
@private_access_errors
def status(request,delivery_id,attempt_id):
    delivery=scope(request,delivery_id);attempt=attempt_for(delivery,attempt_id)
    error='';code=200
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            if getattr(request,'limited',False):raise ValidationError('Too many status checks. Please wait and try again.')
            if action=='expire' and request.POST.get('confirm')!='yes':raise ValidationError('Confirm closing this unused test checkout.')
            attempt=change_attempt(delivery_id=delivery.pk,attempt_id=attempt.pk,action=action)
            if action in {'continue','retry'} and attempt.state=='open':return handoff(request,delivery,attempt)
        except (ValidationError,GatewayUnavailable) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);code=400
            attempt.refresh_from_db()
        else:return redirect('workqueue:invoice_test_payment',delivery.pk,attempt.pk)
    return private_render(request,'workqueue/client_test_payment.html',{'delivery':delivery,'attempt':attempt,'error':error,
        'config':configuration(),'amounts':balance(delivery.invoice),'fingerprint':fingerprint(attempt),
        'receipt':NativeTestPaymentReceipt.objects.filter(attempt=attempt).first()},status=code,form=True)


@queue_enabled
@require_GET
@private_access_errors
def state(request,delivery_id,attempt_id):
    delivery=scope(request,delivery_id);attempt=attempt_for(delivery,attempt_id)
    return private_response(JsonResponse({'fingerprint':fingerprint(attempt)}))


def pdf_response(receipt):
    from .payment_receipt_pdf import receipt_pdf
    response=HttpResponse(receipt_pdf(receipt_projection(receipt)),content_type='application/pdf')
    response['Content-Disposition']=f'attachment; filename="{receipt.number}-test-only.pdf"'
    return private_response(response)


@queue_enabled
@require_GET
@private_access_errors
def receipt(request,delivery_id,receipt_id):
    delivery=session_delivery(request,delivery_id)
    if not delivery:raise PermissionDenied
    receipt=get_object_or_404(NativeTestPaymentReceipt,pk=receipt_id,attempt__invoice=delivery.invoice)
    return pdf_response(receipt)


@staff_view
@require_GET
def staff_receipt(request,receipt_id):
    return pdf_response(get_object_or_404(NativeTestPaymentReceipt,pk=receipt_id))
