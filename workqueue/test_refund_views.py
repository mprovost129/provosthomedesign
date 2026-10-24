import uuid
from decimal import Decimal
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.http import require_http_methods
from .pm_views import staff_view,editable,error_text
from .services import EditConflict
from .test_payment_models import NativePaymentAttempt
from .test_refund_models import NativeRefundRequest
from .test_payment_gateway import configuration,GatewayUnavailable
from .test_payment_views import private
from .test_payments import refresh
from .test_refunds import refund_owner,reserve_refund,submit_refund

SALT='phd-sandbox-refund-review-v1'


class RefundForm(forms.Form):
    token=forms.UUIDField(widget=forms.HiddenInput)
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    amount=forms.DecimalField(min_value=Decimal('.01'),max_value=Decimal('999999.99'),max_digits=14,decimal_places=2,label='Test refund amount (USD)')
    treatment=forms.ChoiceField(choices=[('refund','Return payment only — amount becomes due again'),('credit','Return payment and credit canceled work — amount is no longer owed')],label='How should this affect the invoice?')
    note=forms.CharField(max_length=2000,widget=forms.Textarea,label='Internal reason')


@staff_view
@require_http_methods(['GET','POST'])
def prepare(request,attempt_id):
    editable(request)
    attempt=get_object_or_404(NativePaymentAttempt.objects.select_related('invoice'),pk=attempt_id)
    form=RefundForm(request.POST or None,initial={'token':uuid.uuid4(),'version':attempt.invoice.version,'treatment':'refund'})
    error='';review=None;status=200
    if request.method=='POST':
        refund_owner(request.user);status=400
        if request.POST.get('action')=='confirm':
            try:
                if request.POST.get('confirm')!='yes':raise ValidationError('Confirm this test refund after reviewing the amount and credit choice.')
                data=signing.loads(request.POST.get('review',''),salt=SALT,max_age=900)
                if data['actor']!=request.user.pk or data['attempt']!=str(attempt.pk):raise ValidationError('Review this refund again in your own session.')
                refresh(attempt)
                record=reserve_refund(attempt_id=attempt.pk,version=data['version'],amount=data['amount'],credit_work=data['credit_work'],
                    note=data['note'],token=uuid.UUID(data['token']),actor=request.user)
                submit_refund(record,actor=request.user)
            except signing.BadSignature:
                error='This refund review expired or changed. Enter and review the details again.'
            except (ValidationError,GatewayUnavailable,EditConflict) as exc:
                existing=attempt.refund_requests.filter(state__in=['creating','pending','requires_action','review']).first()
                if existing:
                    messages.warning(request,error_text(exc) if isinstance(exc,ValidationError) else str(exc))
                    return redirect('workqueue:test_refund_detail',existing.pk)
                error=error_text(exc) if isinstance(exc,ValidationError) else str(exc)
                status=409 if isinstance(exc,EditConflict) else 400
            else:
                return redirect('workqueue:test_refund_detail',record.pk)
        elif form.is_valid():
            data=form.cleaned_data
            review={'actor':request.user.pk,'attempt':str(attempt.pk),'token':str(data['token']),'version':data['version'],
                'amount':str(data['amount']),'credit_work':data['treatment']=='credit','note':data['note']}
            review['signed']=signing.dumps(review,salt=SALT);status=200
    return private(render(request,'workqueue/test_refund_prepare.html',{'attempt':attempt,'form':form,'error':error,'review':review,'config':configuration()},status=status))


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,refund_id):
    record=get_object_or_404(NativeRefundRequest.objects.select_related('attempt__invoice','actor'),pk=refund_id)
    error='';status=200
    if request.method=='POST':
        editable(request)
        try:
            if request.POST.get('action')=='retry':submit_refund(record,actor=request.user)
            elif request.POST.get('action')=='refresh':refresh(record.attempt)
            else:raise ValidationError('Choose a refund status action.')
        except (ValidationError,GatewayUnavailable) as exc:
            error=error_text(exc) if isinstance(exc,ValidationError) else str(exc);status=400
        record.refresh_from_db()
    return private(render(request,'workqueue/test_refund_detail.html',{'record':record,'error':error,'config':configuration()},status=status))
