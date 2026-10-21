import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode,urlsplit
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from .staff_security_models import StaffOTPDevice,StaffRecoveryCode


def recovery_digest(device,code):
    return hashlib.sha256((str(device.pk)+':'+code.strip()).encode()).hexdigest()


def make_recovery_codes(device):
    device.recovery_codes.all().delete()
    codes=[secrets.token_urlsafe(12) for _ in range(10)]
    StaffRecoveryCode.objects.bulk_create([StaffRecoveryCode(device=device,digest=recovery_digest(device,code)) for code in codes])
    return codes


def security_notice(user,text):
    from .models import NotificationDelivery
    from django import forms
    try:email=forms.EmailField().clean(user.email)
    except ValidationError:return
    NotificationDelivery.objects.create(recipient_kind='owner',recipient=email,
        subject='Provost Home Design staff security changed',body=text+'\n\nIf you did not make this change, contact the website administrator immediately.',
        provider_reference='staffsecurity-'+secrets.token_hex(16))


def verify_locked(device,token):
    allowed,_=device.verify_is_allowed()
    if not allowed:return False
    code=device.recovery_codes.select_for_update().filter(digest=recovery_digest(device,token),used_at__isnull=True).first()
    if code:
        code.used_at=timezone.now();code.save(update_fields=['used_at'])
        device.throttle_reset(commit=False);device.set_last_used_timestamp(commit=False);device.save()
        return True
    return device.verify_token(token)


@transaction.atomic
def verify_staff_token(user,token):
    device=StaffOTPDevice.objects.select_for_update().filter(user=user,confirmed=True).first()
    return device if device and verify_locked(device,token) else None


def safe_next(request,value):
    if value and url_has_allowed_host_and_scheme(value,{request.get_host()},require_https=request.is_secure()):
        path=urlsplit(value).path
        if path.startswith(('/dashboard/','/work-queue/','/work-projects/','/clients/','/admin/')) and path not in {'/admin/login/','/admin/logout/'}:
            return value
    return reverse('workqueue:dashboard')


def enrollment_stamp(device):
    return salted_hmac('phd-staff-enrollment',str(device.pk)+':'+device.key).hexdigest()


@transaction.atomic
def start_enrollment(user,password):
    # Serialize enrollment and security changes for this account, even before
    # a device exists. A confirmed factor is never replaced by this endpoint.
    user=get_user_model().objects.select_for_update().get(pk=user.pk)
    if not user.check_password(password):raise ValidationError('Check your password and try again.')
    device=StaffOTPDevice.objects.select_for_update().filter(user=user).first()
    if device and device.confirmed:raise ValidationError('Two-step sign-in is already enabled.')
    if device:device.delete()
    return StaffOTPDevice.objects.create(user=user,name='Staff authenticator',confirmed=False)


@transaction.atomic
def confirm_enrollment(user,token,stamp):
    device=StaffOTPDevice.objects.select_for_update().filter(user=user,confirmed=False).first()
    if (not device or device.enrollment_expires_at < timezone.now()
            or not secrets.compare_digest(stamp,enrollment_stamp(device))):
        raise ValidationError('Setup expired. Start again with your password.')
    if not device.verify_token(token):return None,[]
    device.confirmed=True;device.save(update_fields=['confirmed'])
    codes=make_recovery_codes(device)
    security_notice(user,'Two-step sign-in was enabled on your staff account. Keep your recovery codes somewhere safe. No codes or setup secrets are included in this email.')
    return device,codes


@transaction.atomic
def change_factor(user,password,token,disable=False):
    user=get_user_model().objects.select_for_update().get(pk=user.pk)
    if not user.check_password(password):raise ValidationError('Check your password and try again.')
    device=StaffOTPDevice.objects.select_for_update().filter(user=user,confirmed=True).first()
    if not device or not verify_locked(device,token):return False,[]
    if disable:
        device.delete()
        security_notice(user,'Two-step sign-in was disabled on your staff account.')
        return True,[]
    codes=make_recovery_codes(device)
    security_notice(user,'New staff recovery codes were generated. Previously generated recovery codes no longer work.')
    return True,codes


class StaffMFAMiddleware:
    """An enrolled staff account cannot bypass OTP through admin or file URLs."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):return self.get_response(request)
    def process_view(self,request,view,args,kwargs):
        user=request.user
        if not user.is_authenticated or not user.is_active or not user.is_staff:return
        if user.is_verified():return
        match=request.resolver_match
        if match and match.view_name in {'workqueue:staff_login','workqueue:staff_logout',
                'workqueue:staff_security','workqueue:staff_mfa_verify','workqueue:staff_password_reset',
                'workqueue:staff_password_reset_done','workqueue:staff_password_reset_confirm',
                'workqueue:staff_password_reset_complete','admin:logout'}:return
        if not StaffOTPDevice.objects.filter(user=user,confirmed=True).exists():return
        target=reverse('workqueue:staff_mfa_verify',urlconf='config.urls')
        if getattr(request,'urlconf','')=='config.web_urls':
            return redirect(getattr(settings,'INTAKE_PUBLIC_BASE_URL','').rstrip('/')+target)
        destination=request.path if any(key in request.GET for key in ('code','state','token','access_token')) else request.get_full_path()
        return redirect(target+'?'+urlencode({'next':destination}))
