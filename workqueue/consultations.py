"""Email-verified prospect inquiries with immediate access to booking."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .access import mint_email_token, normalize_email
from .booking import lock_bookings
from .crm_identity import normalized_text, phone_key
from .models import Client, ClientContact, ClientEvent, ConsultationRequest, NotificationDelivery
from .notifications import access_url, site_url
from .services import lock_queue


def notice(item, kind, subject, body):
    return NotificationDelivery.objects.create(recipient_kind='owner' if kind=='owner' else 'client',
        recipient=settings.INTAKE_OWNER_EMAIL if kind=='owner' else item.email, subject=subject, body=body,
        provider_reference='consultation-'+uuid.uuid4().hex)


@transaction.atomic
def submit_consultation(*, email, full_name, phone, description, key, verified=False, terms_url=''):
    lock_bookings()
    email=normalize_email(email)
    from django.core.validators import validate_email
    validate_email(email)
    if not all(value.strip() for value in [full_name,phone,description]) or len(full_name)>200 or len(phone)>50 or len(description)>3000:
        raise ValidationError('Provide your name, phone number and a brief description of your project.')
    item=ConsultationRequest.objects.filter(idempotency_key=key).first()
    if item:
        if item.email!=email:
            raise ValidationError('Reload the form and try again.')
        return item
    item=ConsultationRequest.objects.select_for_update().filter(email=email,state__in=['unverified','pending']).first()
    if item:
        if verified and item.state=='unverified':
            verify_consultation(item.pk.hex,email)
            item.refresh_from_db()
        return item
    item=ConsultationRequest.objects.create(email=email,full_name=full_name.strip(),phone=phone.strip(),
        description=description.strip(),idempotency_key=key,terms_url=terms_url,terms_accepted_at=timezone.now())
    if verified:
        verify_consultation(item.pk.hex,email)
        item.refresh_from_db()
    else:
        queue_verification(item)
    return item


def queue_verification(item):
    _,raw=mint_email_token(item.email,item.pk.hex,destination='consult')
    item.verification_delivery=notice(item,'client','Verify your consultation request',
        f'Hello {item.full_name},\n\nVerify your email to choose an initial consultation time:\n{access_url(raw)}\n\n'
        'This link expires in 24 hours and can be used once. No appointment time is reserved yet. '
        'After verification, you can choose an available time without waiting for approval.\n\n'
        f'Your project: {item.description}\n\nIf you did not request this, you can ignore this email.\n')
    item.save(update_fields=['verification_delivery'])


@transaction.atomic
def verify_consultation(reference,email):
    lock_bookings()
    try:
        request_id=uuid.UUID(reference)
    except (ValueError,TypeError):
        raise ValidationError('That consultation verification link is invalid.') from None
    item=ConsultationRequest.objects.select_for_update().filter(pk=request_id,email=normalize_email(email)).first()
    if not item:
        raise ValidationError('That consultation verification link is invalid.')
    if item.state!='unverified':
        return item
    lock_queue()
    contact=ClientContact.objects.filter(email__iexact=item.email,archived_at__isnull=True,
        client__merged_into__isnull=True).select_related('client').order_by('created_at','pk').first()
    if contact is None:
        account=Client.objects.create(name=item.full_name,normalized_name=normalized_text(item.full_name),
            kind=Client.Kind.INDIVIDUAL,stage=Client.Stage.INQUIRY)
        contact=ClientContact.objects.create(client=account,email=item.email,full_name=item.full_name,phone=item.phone,
            submitted_company='Homeowner',normalized_name=normalized_text(item.full_name),normalized_phone=phone_key(item.phone),
            booking_requires_approval=False)
    item.contact=contact
    item.verified_at=timezone.now()
    item.state=ConsultationRequest.State.READY
    ClientEvent.objects.create(client=contact.client,action='Consultation requested',changes={
        'consultation':str(item.pk),'description':item.description})
    item.owner_delivery=notice(item,'owner','Consultation request: '+item.full_name,
        f'Email-verified consultation request\nName: {item.full_name}\nEmail: {item.email}\nPhone: {item.phone}\n\n'
        f'Project: {item.description}\n\nThis visitor can choose a time now. No approval is needed.\n'+
        site_url(reverse('workqueue:queue'))+'#consultations\n')
    item.receipt_delivery=notice(item,'client','Consultation request received',
        f'Hello {item.full_name},\n\nYour email is verified and we received your consultation request.\n\n'
        f'Project: {item.description}\n\nChoose an available appointment time:\n'+site_url(reverse('workqueue:book'))+
        '\n\nNo approval is needed. A time is reserved only when you request an appointment.\n')
    item.save(update_fields=['contact','verified_at','state','owner_delivery','receipt_delivery'])
    return item


@transaction.atomic
def resend_verification(request_id,email):
    lock_bookings()
    item=ConsultationRequest.objects.select_for_update().get(pk=request_id,email=normalize_email(email),state='unverified')
    queue_verification(item)
    return item


def decide_consultation(request_id,approve,actor):
    """Reject stale approval actions without sending obsolete decision emails."""
    from .crm import require_staff
    require_staff(actor,'workqueue.change_bookingaccess')
    raise ValidationError('Booking approval is no longer required. Verified visitors can choose a time; manage cancellations under appointments.')
