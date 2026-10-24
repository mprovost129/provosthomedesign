import hashlib
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from .crm import require_staff
from .models import NotificationDelivery
from .native_invoice_models import NativeInvoice,NativeInvoiceEvent
from .invoice_delivery_models import NativeInvoiceDelivery
from .native_invoice_document import test_invoice_snapshot
from .native_invoices import checked_invoice,text
from .notifications import site_url
from .pm_services import current_client
from .services import lock_queue


def owner_test_address():
    address=str(getattr(settings,'INTAKE_OWNER_EMAIL','')).strip().lower()
    try:validate_email(address)
    except ValidationError:
        raise ValidationError('The owner email address must be configured before sending test copies.') from None
    return address


def contacts_for(invoice):
    client=current_client(invoice.plan.document.client)
    return client.contacts.filter(archived_at__isnull=True).exclude(email__isnull=True).exclude(email='').order_by('full_name','email')


def reviewed_contact(invoice,contact_id):
    contact=contacts_for(invoice).filter(pk=contact_id).first()
    if not contact:
        raise ValidationError('Choose an active email contact belonging to this invoice client.')
    validate_email(contact.email)
    return contact


def delivery_context(delivery,link):
    return {'delivery':delivery,'snapshot':delivery.snapshot,'link':link,
        'expiry':delivery.expires_at,'issuer':delivery.snapshot.get('issuer',{})}


def active_delivery(delivery):
    return not delivery.revoked_at and delivery.expires_at>timezone.now() and not delivery.invoice.voided_at


def cancel_ineligible_invoice_notice(record):
    """Runs while the outbox row is locked; never takes QueueState in this order."""
    if not record.provider_reference.startswith('invoice-test-'):
        return False
    delivery=NativeInvoiceDelivery.objects.select_related('invoice').filter(notification=record).first()
    valid=delivery and active_delivery(delivery)
    if valid:
        try:
            # A changed owner address cannot redirect an already reviewed test.
            valid=(record.recipient.lower()==delivery.owner_recipient.lower()==owner_test_address()
                and not record.to_recipients and not record.cc_recipients and not record.bcc_recipients)
        except ValidationError:valid=False
    if valid:return False
    record.state='canceled';record.body='';record.html_body=''
    record.last_error='Invoice test copy canceled: link unavailable or owner email settings changed.'
    record.save(update_fields=['state','body','html_body','last_error'])
    return True


@transaction.atomic
def queue_invoice_test(*,invoice_id,version,contact_id,contact_email,contact_name,subject,message,owner_email,token,actor,allow_test_checkout=False):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    if type(allow_test_checkout) is not bool:raise ValidationError('Choose whether this test copy permits sandbox checkout.')
    if allow_test_checkout:
        from .client_payments import require_checkout_enabled
        require_checkout_enabled()
        if not actor.is_superuser:raise ValidationError('Only the owner may enable sandbox checkout on a private test copy.')
    subject=text(subject,180,'the subject');message=text(message,10000,'the message',required=False)
    if '\r' in subject or '\n' in subject:
        raise ValidationError('Use a single-line email subject.')
    target=owner_test_address()
    if owner_email!=target:
        raise ValidationError('The owner email changed after review. Review the message again.')
    subject=f'[TEST ONLY] {NativeInvoice.objects.get(pk=invoice_id).number} - {subject}'
    existing=NativeInvoiceDelivery.objects.filter(pk=token).first()
    if existing:
        if (existing.invoice_id!=invoice_id or existing.actor_id!=actor.pk or existing.invoice_version!=version
            or existing.intended_contact_id!=contact_id or existing.intended_email!=contact_email or existing.intended_name!=contact_name or existing.subject!=subject
            or existing.message!=message or existing.owner_recipient!=target or existing.allow_test_checkout!=allow_test_checkout):
            raise ValidationError('This send reference was already used for different details. Start a new review.')
        return existing
    invoice=checked_invoice(invoice_id,version)
    contact=reviewed_contact(invoice,contact_id)
    if contact.email!=contact_email or (contact.full_name or invoice.plan.document.client.name)!=contact_name:
        raise ValidationError('The intended contact changed after review. Review the message again.')
    if invoice.deliveries.filter(notification__state__in=['pending','sending','unknown']).exists():
        raise ValidationError('A copy is queued, sending, or has an uncertain result. Check delivery history before sending again.')
    # Each explicit resend rotates links. Financial records and snapshots remain.
    for old in invoice.deliveries.filter(revoked_at__isnull=True):
        old.revoked_at=timezone.now();old.revoked_reason='Replaced by a newer test copy.'
        old.save(update_fields=['revoked_at','revoked_reason'])
    raw=secrets.token_urlsafe(32)
    delivery=NativeInvoiceDelivery(id=token,invoice=invoice,invoice_version=version,allow_test_checkout=allow_test_checkout,snapshot=test_invoice_snapshot(invoice),
        intended_contact=contact,intended_name=contact.full_name or invoice.plan.document.client.name,intended_email=contact.email,
        owner_recipient=target,subject=subject,message=message,digest=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=timezone.now()+timedelta(days=7),actor=actor)
    link=site_url(reverse('workqueue:invoice_test_access'))+'#'+urlencode({'token':raw})
    context=delivery_context(delivery,link)
    notice=NotificationDelivery.objects.create(recipient_kind='owner',recipient=target,subject=subject,
        body=render_to_string('workqueue/invoice_test_email.txt',context),
        html_body=render_to_string('workqueue/invoice_test_email.html',context),provider_reference='invoice-test-'+uuid.uuid4().hex)
    delivery.notification=notice;delivery.full_clean();delivery.save()
    invoice.version+=1;invoice.save(update_fields=['version'])
    NativeInvoiceEvent.objects.create(invoice=invoice,action='Test email queued',actor=actor,
        details={'delivery':str(delivery.pk),'owner':target,'intended_email':contact.email})
    return delivery


@transaction.atomic
def revoke_delivery(*,delivery_id,actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    delivery=NativeInvoiceDelivery.objects.select_related('invoice').get(pk=delivery_id)
    if delivery.revoked_at:return delivery
    delivery.revoked_at=timezone.now();delivery.revoked_reason='Revoked by staff.'
    delivery.save(update_fields=['revoked_at','revoked_reason'])
    NotificationDelivery.objects.filter(pk=delivery.notification_id,state='pending').update(state='canceled',body='',html_body='',last_error='Invoice test link revoked before delivery.')
    NativeInvoiceEvent.objects.create(invoice=delivery.invoice,action='Test link revoked',actor=actor,details={'delivery':str(delivery.pk)})
    return delivery


@transaction.atomic
def open_delivery(raw):
    if not isinstance(raw,str) or not 20<=len(raw)<=100:return None
    lock_queue()
    delivery=NativeInvoiceDelivery.objects.select_related('invoice').filter(digest=hashlib.sha256(raw.encode()).hexdigest()).first()
    if not delivery or not active_delivery(delivery):return None
    if not delivery.opened_at:
        delivery.opened_at=timezone.now();delivery.save(update_fields=['opened_at'])
        NativeInvoiceEvent.objects.create(invoice=delivery.invoice,action='Test link opened',details={'delivery':str(delivery.pk)})
    return delivery


def session_delivery(request,delivery_id):
    if request.session.get('invoice_test_delivery')!=str(delivery_id):return None
    delivery=NativeInvoiceDelivery.objects.select_related('invoice').filter(pk=delivery_id).first()
    return delivery if delivery and active_delivery(delivery) else None
