import uuid
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from .models import ClientContact
from .native_invoice_models import NativeInvoice
from .invoice_delivery_models import NativeInvoiceDelivery
from .invoice_delivery import (contacts_for,owner_test_address,queue_invoice_test,revoke_delivery,
    open_delivery,session_delivery)
from .native_invoice_document import test_invoice_snapshot
from .native_invoices import balance
from .pm_views import staff_view,editable,error_text,private_response
from .services import EditConflict
from .views import queue_enabled

SALT='workqueue.invoice-test-review.v1'


class ComposeForm(forms.Form):
    token=forms.UUIDField(widget=forms.HiddenInput)
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    contact=forms.ModelChoiceField(queryset=ClientContact.objects.none(),label='Intended client contact (review only)')
    subject=forms.CharField(max_length=180,label='Subject',help_text='TEST ONLY and the invoice number are added automatically.')
    message=forms.CharField(required=False,max_length=10000,widget=forms.Textarea(attrs={'rows':6}),label='Client-facing message',help_text='Only the reviewed message and invoice details are included. Internal accounting notes stay private.')

    def __init__(self,*args,invoice,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['contact'].queryset=contacts_for(invoice)
        self.fields['contact'].label_from_instance=lambda contact:f'{contact.full_name or invoice.plan.document.client.name} — {contact.email}'

    def clean_subject(self):
        value=self.cleaned_data['subject']
        if '\n' in value or '\r' in value:raise ValidationError('Use a single-line subject.')
        return value


class ConfirmForm(forms.Form):
    review=forms.CharField(widget=forms.HiddenInput,max_length=100000)
    confirm=forms.BooleanField(label='Send this TEST copy only to the owner inbox shown above. Do not email the client.')


def review_payload(request,invoice,form):
    data=form.cleaned_data;contact=data['contact']
    return {'actor':str(request.user.pk),'invoice':str(invoice.pk),'version':data['version'],
        'token':str(data['token']),'contact':str(contact.pk),'contact_email':contact.email,
        'contact_name':contact.full_name or invoice.plan.document.client.name,
        'subject':data['subject'],'message':data['message'],'owner_email':owner_test_address()}


@staff_view
@require_http_methods(['GET','POST'])
def compose(request,invoice_id):
    editable(request)
    invoice=get_object_or_404(NativeInvoice.objects.select_related('plan__document__client'),pk=invoice_id)
    form=ComposeForm(request.POST if request.method=='POST' and request.POST.get('action')=='review' else None,
        invoice=invoice,initial={'token':uuid.uuid4(),'version':invoice.version,'subject':invoice.snapshot['title'][:180],
            'message':'Please review the enclosed project invoice. This is a test copy for checking the delivery experience; no payment is due.'})
    confirm=ConfirmForm(request.POST if request.method=='POST' and request.POST.get('action')=='send' else None)
    error='';status=200;review=None;preview=None
    try:target=owner_test_address()
    except ValidationError as exc:target='';error=error_text(exc)
    if invoice.voided_at:error='This test invoice is voided and cannot be sent.'
    if request.method=='POST':
        status=400
        action=request.POST.get('action')
        if action=='review' and form.is_valid() and not error:
            if form.cleaned_data['version']!=invoice.version:
                form.add_error(None,'The invoice changed. Reload before reviewing the message.');status=409
            else:
                payload=review_payload(request,invoice,form)
                review=signing.dumps(payload,salt=SALT,compress=True)
                confirm=ConfirmForm(initial={'review':review})
                preview={'subject':f"[TEST ONLY] {invoice.number} - {payload['subject']}",'message':payload['message'],
                    'intended_name':payload['contact_name'],'intended_email':payload['contact_email'],'owner_recipient':target}
                status=200
        elif action=='send' and not error:
            try:
                payload=signing.loads(request.POST.get('review',''),salt=SALT,max_age=900)
                if not isinstance(payload,dict) or payload.get('actor')!=str(request.user.pk) or payload.get('invoice')!=str(invoice.pk):
                    raise ValidationError('This review belongs to another staff session or invoice.')
                preview={'subject':f"[TEST ONLY] {invoice.number} - {payload['subject']}",'message':payload['message'],
                    'intended_name':payload['contact_name'],'intended_email':payload['contact_email'],'owner_recipient':payload['owner_email']}
                review=request.POST['review']
                if confirm.is_valid():
                    delivery=queue_invoice_test(invoice_id=invoice.pk,version=payload['version'],contact_id=uuid.UUID(payload['contact']),
                        contact_email=payload['contact_email'],contact_name=payload['contact_name'],subject=payload['subject'],message=payload['message'],
                        owner_email=payload['owner_email'],token=uuid.UUID(payload['token']),actor=request.user)
                else:
                    delivery=None
            except (signing.BadSignature,KeyError,TypeError,ValueError):
                error='This email review expired or changed. Start a fresh review.'
            except (ValidationError,EditConflict) as exc:
                error=error_text(exc);status=409 if isinstance(exc,EditConflict) else 400
            else:
                if delivery:
                    messages.success(request,'Test copy queued for your owner inbox. The intended client was not emailed.')
                    return redirect('workqueue:invoice_delivery_detail',delivery.pk)
        elif action not in {'review','send'}:
            error='Choose Review email or Send test copy.'
    snapshot=test_invoice_snapshot(invoice)
    return render(request,'workqueue/invoice_delivery_compose.html',{'invoice':invoice,'form':form,'confirm_form':confirm,
        'review':review,'preview':preview,'preview_expiry':timezone.now()+timedelta(days=7),'target':target,'error':error,'snapshot':snapshot},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,delivery_id):
    delivery=get_object_or_404(NativeInvoiceDelivery.objects.select_related('invoice','notification'),pk=delivery_id)
    if request.method=='POST':
        editable(request)
        if request.POST.get('action')!='revoke' or request.POST.get('confirm')!='yes':
            return render(request,'workqueue/invoice_delivery_detail.html',{'delivery':delivery,'error':'Confirm revoking this private link.'},status=400)
        revoke_delivery(delivery_id=delivery.pk,actor=request.user)
        messages.success(request,'Private link revoked. A pending email was canceled; messages already sending or delivered cannot be recalled.')
        return redirect('workqueue:invoice_delivery_detail',delivery.pk)
    return render(request,'workqueue/invoice_delivery_detail.html',{'delivery':delivery})


@queue_enabled
@require_http_methods(['GET','POST'])
@ratelimit(key='ip',rate='20/h',method='POST',block=False)
def access(request):
    error='';status=200
    if request.method=='POST':
        status=400
        if getattr(request,'limited',False):error='Too many attempts. Please try again later.';status=429
        else:
            delivery=open_delivery(request.POST.get('token',''))
            if delivery:
                request.session['invoice_test_delivery']=str(delivery.pk);request.session.cycle_key()
                return private_response(redirect('workqueue:invoice_test_public',delivery.pk))
            error='This private link is unavailable or expired. Contact Provost Home Design for a new test copy.'
    return private_response(render(request,'workqueue/invoice_test_access.html',{'error':error},status=status),has_form=True)


@queue_enabled
@require_http_methods(['GET'])
def public(request,delivery_id):
    delivery=session_delivery(request,delivery_id)
    if not delivery:
        return private_response(render(request,'workqueue/invoice_test_access.html',{'error':'Open the complete private link from your invoice test email.'},status=403),has_form=True)
    return private_response(render(request,'workqueue/invoice_test_public.html',{'delivery':delivery,'snapshot':delivery.snapshot,
        'amounts':balance(delivery.invoice)}))


@queue_enabled
@require_http_methods(['GET'])
def public_pdf(request,delivery_id):
    delivery=session_delivery(request,delivery_id)
    if not delivery:return private_response(HttpResponse('Open your private invoice test link first.',status=403))
    from .document_pdf import document_pdf
    response=HttpResponse(document_pdf(delivery.snapshot,invoice_draft=True,test_invoice=True),content_type='application/pdf')
    response['Content-Disposition']=f'attachment; filename="{delivery.snapshot["number"]}-test-only.pdf"'
    return private_response(response)
