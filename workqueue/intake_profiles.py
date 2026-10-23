"""Small, verified CRM contact projections; never a public email directory.

The remembered-device capability cannot authenticate portal, files or bookings.
CRM associations are checked on every request, so archived/reassigned contacts
and merged clients do not retain stale access through a remembered device.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .access import normalize_email, verified_email
from .intake_profile_models import IntakeDevice
from .models import Client, ClientContact

COOKIE = 'phd_intake_device'
REMEMBER_DAYS = 90
CONTACT_FIELDS = ('contact_full_name', 'company', 'contact_email', 'contact_phone',
                  'billing_street', 'billing_city', 'billing_state', 'billing_zip')


def device_record(request):
    raw = request.COOKIES.get(COOKIE, '')
    if not raw or len(raw) > 100:
        return None
    return IntakeDevice.objects.filter(digest=hashlib.sha256(raw.encode()).hexdigest(),
        revoked_at__isnull=True, expires_at__gt=timezone.now()).first()


def intake_identity(request):
    email = verified_email(request)
    if email:
        return normalize_email(email)
    device = device_record(request)
    return device.email if device else ''


def profiles_for(email):
    if not email:
        return []
    email = normalize_email(email)
    contacts = ClientContact.objects.filter(email__iexact=email, archived_at__isnull=True,
        client__merged_into__isnull=True).select_related('client').order_by('client__name', 'id')
    profiles = []
    for contact in contacts:
        account = contact.client
        company = account.name if account.kind == Client.Kind.COMPANY else 'Homeowner'
        values = dict(contact_full_name=contact.full_name or (account.name if account.kind == Client.Kind.INDIVIDUAL else ''),
                      company=company, contact_email=email, contact_phone=contact.phone)
        values.update({field: getattr(account, field) for field in CONTACT_FIELDS if field.startswith('billing_')})
        profiles.append({'id': str(contact.pk), 'label': f'{company} — {contact.full_name or email}', 'values': values})
    return profiles


def revoke_device(request):
    raw = request.COOKIES.get(COOKIE, '')
    if raw and len(raw) <= 100:
        IntakeDevice.objects.filter(digest=hashlib.sha256(raw.encode()).hexdigest(), revoked_at__isnull=True).update(revoked_at=timezone.now())


def forget_cookie(response):
    response.delete_cookie(COOKIE, path='/', samesite='Lax')
    return response


def remember_device(request, response, email):
    revoke_device(request)
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    IntakeDevice.objects.create(email=normalize_email(email), digest=hashlib.sha256(raw.encode()).hexdigest(),
                               expires_at=now + timedelta(days=REMEMBER_DAYS))
    # This cookie carries only a random capability. Contact details stay in CRM.
    response.set_cookie(COOKIE, raw, max_age=REMEMBER_DAYS * 86400, path='/',
                        secure=not settings.DEBUG, httponly=True, samesite='Lax')
    IntakeDevice.objects.filter(expires_at__lt=now - timedelta(days=7)).delete()
    return response
