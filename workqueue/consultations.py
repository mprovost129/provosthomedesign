"""Email-verified prospect inquiries; first booking requires staff approval."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .access import mint_email_token, normalize_email
from .booking import eligible, lock_bookings, set_booking_access
from .crm_identity import normalized_text, phone_key
from .models import Client, ClientContact, ClientEvent, ConsultationRequest, NotificationDelivery, BookingAudit
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
        f'Hello {item.full_name},\n\nVerify your email to send Mike your initial consultation request:\n{access_url(raw)}\n\n'
        'This link expires in 24 hours and can be used once. No appointment time is reserved yet. '
        'After verification, Mike will review your request before you choose a time.\n\n'
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
    already_allowed=eligible(item.email)
    if contact is None:
        account=Client.objects.create(name=item.full_name,normalized_name=normalized_text(item.full_name),
            kind=Client.Kind.INDIVIDUAL,stage=Client.Stage.INQUIRY)
        contact=ClientContact.objects.create(client=account,email=item.email,full_name=item.full_name,phone=item.phone,
            submitted_company='Homeowner',normalized_name=normalized_text(item.full_name),normalized_phone=phone_key(item.phone),
            booking_requires_approval=not already_allowed)
    item.contact=contact
    item.verified_at=timezone.now()
    item.state='approved' if already_allowed else 'pending'
    if already_allowed:
        item.decided_at=timezone.now()
    ClientEvent.objects.create(client=contact.client,action='Consultation requested',changes={
        'consultation':str(item.pk),'description':item.description})
    item.owner_delivery=notice(item,'owner','Consultation request: '+item.full_name,
        f'Email-verified consultation request\nName: {item.full_name}\nEmail: {item.email}\nPhone: {item.phone}\n\n'
        f'Project: {item.description}\n\n'+('Existing booking access is enabled.\n' if already_allowed else 'Approve or decline first-booking access:\n')+
        site_url(reverse('workqueue:queue'))+'#consultations\n')
    item.receipt_delivery=notice(item,'client','Consultation request received',
        f'Hello {item.full_name},\n\nYour email is verified and we received your consultation request.\n\n'
        f'Project: {item.description}\n\n'+('You already have booking access. Choose a time:\n'+site_url(reverse('workqueue:book'))
        if already_allowed else 'Mike will review your request. We will email you when you can choose a time. No time is reserved yet.')+'\n')
    item.save(update_fields=['contact','verified_at','state','decided_at','owner_delivery','receipt_delivery'])
    return item


@transaction.atomic
def resend_verification(request_id,email):
    lock_bookings()
    item=ConsultationRequest.objects.select_for_update().get(pk=request_id,email=normalize_email(email),state='unverified')
    queue_verification(item)
    return item


@transaction.atomic
def decide_consultation(request_id,approve,actor):
    from .crm import require_staff
    require_staff(actor,'workqueue.change_bookingaccess')
    lock_bookings()
    lock_queue()
    item=ConsultationRequest.objects.select_for_update(of=('self',)).select_related('contact__client').get(pk=request_id)
    state='approved' if approve else 'declined'
    if item.state==state:
        return item
    if item.state!='pending' or not item.verified_at:
        raise ValidationError('This consultation request has already been reviewed or is not verified yet.')
    if approve and (not item.contact or item.contact.archived_at or item.contact.client.merged_into_id or normalize_email(item.contact.email or '')!=item.email):
        raise ValidationError('The client contact changed. Review the CRM record before approving this request.')
    if approve:
        set_booking_access(item.email,True,actor)
        item.contact.booking_requires_approval=False
        item.contact.save(update_fields=['booking_requires_approval'])
        _,raw=mint_email_token(item.email,destination='booking')
        body=f'Hello {item.full_name},\n\nYour initial consultation request is approved. Choose an available appointment time:\n{access_url(raw)}\n\n'
        body+='This sign-in link expires in 24 hours and can be used once. Approval does not reserve a time.\n'
    else:
        body=f'Hello {item.full_name},\n\nWe are unable to offer an initial consultation for this request at this time. '
        body+='Please contact Provost Home Design if you would like to discuss other options.\n'
    item.decision_delivery=notice(item,'client','Consultation request '+state,body)
    item.state=state;item.decided_at=timezone.now();item.decided_by=actor
    item.save(update_fields=['state','decided_at','decided_by','decision_delivery'])
    if item.contact:
        ClientEvent.objects.create(client=item.contact.client,actor=actor,action='Consultation '+state,changes={'consultation':str(item.pk)})
    BookingAudit.objects.create(actor=actor,action='consultation_'+state,details={'consultation':str(item.pk),'email':item.email})
    return item
