import uuid
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q,Count
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_http_methods
from .billing_mail import current_settings,save_settings,queue_receipt,delivery_action,require_owner,message_context,receipt_query
from .billing_mail_models import NativeReceiptEmail,NativeBillingMailEvent
from .invoice_delivery_models import NativeInvoiceDelivery
from .invoice_delivery import owner_test_address
from .pm_views import staff_view,error_text,editable
from .services import EditConflict

SALT='workqueue.receipt-mail-review.v1'


class SettingsForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    automatic_receipts=forms.BooleanField(required=False,label='Automatically email future sandbox payment receipts to my owner inbox')


class SendForm(forms.Form):
    review=forms.CharField(widget=forms.HiddenInput,max_length=10000)
    confirm=forms.BooleanField(label='Email this TEST receipt PDF only to the owner inbox shown above. No client email or real payment.')


class ActionForm(forms.Form):
    expected_state=forms.CharField(max_length=20,widget=forms.HiddenInput)
    attempts=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    action=forms.ChoiceField(choices=[('retry','Retry failed delivery'),('cancel','Cancel this email'),('confirm_sent','I checked my inbox and received this email')])
    confirm=forms.BooleanField(label='I reviewed the email status and confirm this action. For an uncertain send, I checked my inbox first.')


@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    config=current_settings();status=200
    form=SettingsForm(request.POST if request.method=='POST' else None,initial={'version':config.version,'automatic_receipts':config.automatic_receipts})
    if request.method=='POST':
        editable(request);require_owner(request.user);status=400
        if form.is_valid():
            try:save_settings(actor=request.user,**form.cleaned_data)
            except (ValidationError,EditConflict) as exc:form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Sandbox receipt email settings saved. Existing payments are not emailed automatically.')
                return redirect('workqueue:billing_mail')
    q=request.GET.get('q','').strip()[:150];state=request.GET.get('state','')
    states=['pending','sending','sent','failed','unknown','canceled']
    if state not in states:state=''
    receipts=receipt_query().select_related('email_delivery__notification','email_delivery__actor')
    copies=NativeInvoiceDelivery.objects.select_related('notification','invoice')
    if q:
        receipts=receipts.filter(Q(number__icontains=q)|Q(snapshot__invoice_number__icontains=q)|Q(snapshot__client_name__icontains=q)|Q(snapshot__project_name__icontains=q))
        copies=copies.filter(Q(invoice__number__icontains=q)|Q(snapshot__client_name__icontains=q)|Q(snapshot__project_name__icontains=q))
    if state:
        receipts=receipts.filter(email_delivery__notification__state=state);copies=copies.filter(notification__state=state)
    counts={row['notification__state']:row['total'] for row in NativeReceiptEmail.objects.values('notification__state').annotate(total=Count('pk'))}
    try:target=owner_test_address()
    except ValidationError:target='Owner inbox needs configuration'
    return render(request,'workqueue/billing_mail.html',{'form':form,'config':config,'target':target,'q':q,'state':state,
        'states':states,'receipt_page':Paginator(receipts,25).get_page(request.GET.get('page')),'copies':copies[:25],
        'pending_count':counts.get('pending',0)+counts.get('sending',0),'sent_count':counts.get('sent',0),
        'attention_count':counts.get('unknown',0)+counts.get('failed',0),'events':NativeBillingMailEvent.objects.select_related('actor','delivery__receipt')[:20]},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def receipt_email(request,receipt_id):
    receipt=get_object_or_404(receipt_query(),pk=receipt_id)
    delivery=NativeReceiptEmail.objects.select_related('notification','actor').filter(receipt=receipt).first()
    status=200;error=''
    try:context=message_context(receipt);target=context['recipient']
    except ValidationError:context={'receipt':receipt.snapshot};target='';error='Configure the owner inbox before sending a test receipt.'
    review=signing.dumps({'receipt':str(receipt.pk),'owner':target,'actor':str(request.user.pk)},salt=SALT,compress=True)
    send_form=SendForm(request.POST if request.method=='POST' and not delivery else None,initial={'review':review})
    notice=delivery.notification if delivery else None
    actions=[]
    if notice:
        if notice.state=='failed':actions=[('retry','Retry failed delivery'),('cancel','Cancel this email')]
        elif notice.state=='pending':actions=[('cancel','Cancel this email')]
        elif notice.state=='unknown':actions=[('confirm_sent','I checked my inbox and received this email'),('cancel','Close without resending')]
    action_form=ActionForm(request.POST if request.method=='POST' and delivery else None,
        initial={'expected_state':notice.state if notice else '', 'attempts':notice.attempts if notice else 0})
    action_form.fields['action'].choices=actions
    if request.method=='POST':
        editable(request);require_owner(request.user);status=400
        try:
            if delivery:
                if action_form.is_valid():
                    delivery_action(delivery_id=delivery.pk,actor=request.user,**{key:action_form.cleaned_data[key] for key in ['action','expected_state','attempts']})
                else:raise ValidationError('Review and confirm an available email action.')
            elif send_form.is_valid() and not error:
                payload=signing.loads(send_form.cleaned_data['review'],salt=SALT,max_age=900)
                if payload!={'receipt':str(receipt.pk),'owner':owner_test_address(),'actor':str(request.user.pk)}:
                    raise ValidationError('The reviewed recipient or receipt changed. Start a fresh review.')
                queue_receipt(receipt_id=receipt.pk,actor=request.user)
            else:raise ValidationError('Confirm the owner-only test receipt before sending.')
        except (signing.BadSignature,ValidationError,EditConflict) as exc:
            error='The review expired. Start a fresh review.' if isinstance(exc,signing.BadSignature) else error_text(exc)
            status=409 if isinstance(exc,EditConflict) else 400
        else:
            messages.success(request,'Receipt email action saved. No client was emailed.')
            return redirect('workqueue:billing_receipt_email',receipt.pk)
    return render(request,'workqueue/billing_receipt_email.html',{'receipt_record':receipt,'receipt':context['receipt'],'delivery':delivery,
        'notice':notice,'target':target,'send_form':send_form,'action_form':action_form,'actions':actions,'error':error,
        'audit':delivery.events.select_related('actor') if delivery else []},status=status)
