from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import PasswordResetView,PasswordResetDoneView,PasswordResetConfirmView,PasswordResetCompleteView
from django.core.exceptions import ValidationError
from django.shortcuts import redirect,render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from django_otp import login as otp_login,DEVICE_ID_SESSION_KEY
from .staff_security import (safe_next,start_enrollment,confirm_enrollment,change_factor,
                             verify_staff_token,enrollment_stamp)
from .staff_security_models import StaffOTPDevice


def user_key(group,request):return str(request.user.pk)
def email_key(group,request):return request.POST.get('email','').strip().casefold()[:254]


class CodeForm(forms.Form):
    token=forms.CharField(label='Authenticator or recovery code',max_length=40,
        widget=forms.TextInput(attrs={'autocomplete':'one-time-code','autocapitalize':'none','spellcheck':'false'}))


class ChangeSecurityForm(forms.Form):
    password=forms.CharField(label='Current password',widget=forms.PasswordInput(attrs={'autocomplete':'current-password'}))
    token=forms.CharField(label='Authenticator or recovery code',max_length=40,required=False,
        widget=forms.TextInput(attrs={'autocomplete':'one-time-code','autocapitalize':'none','spellcheck':'false'}))


def private_response(response):
    response['Referrer-Policy']='no-referrer'
    response['X-Robots-Tag']='noindex, nofollow'
    return response


@staff_member_required(login_url='workqueue:staff_login')
@never_cache
@sensitive_post_parameters('password','token')
@ratelimit(key='ip',rate='30/5m',method='POST',block=False)
@ratelimit(key=user_key,rate='10/5m',method='POST',block=False)
@require_http_methods(['GET','POST'])
def verify(request):
    target=safe_next(request,request.POST.get('next',request.GET.get('next','')))
    if request.user.is_verified() or not StaffOTPDevice.objects.filter(user=request.user,confirmed=True).exists():
        return redirect(target)
    form=CodeForm(request.POST if request.method=='POST' else None)
    if request.method=='POST':
        if getattr(request,'limited',False):form.add_error(None,'Too many attempts. Please wait five minutes and try again.')
        elif form.is_valid():
            device=verify_staff_token(request.user,form.cleaned_data['token'])
            if device:
                request.session.cycle_key();otp_login(request,device)
                return redirect(target)
            form.add_error('token','The code was not accepted. Wait for a fresh code or use an unused recovery code.')
    return private_response(render(request,'workqueue/staff_verify.html',{'form':form,'next':target},
        status=429 if getattr(request,'limited',False) else 200))


@staff_member_required(login_url='workqueue:staff_login')
@never_cache
@sensitive_post_parameters('password','token')
@ratelimit(key='ip',rate='30/5m',method='POST',block=False)
@ratelimit(key=user_key,rate='10/5m',method='POST',block=False)
@require_http_methods(['GET','POST'])
def security(request):
    form=ChangeSecurityForm(request.POST if request.method=='POST' and request.POST.get('action')!='confirm' else None)
    code_form=CodeForm(request.POST if request.POST.get('action')=='confirm' else None)
    codes=[]
    if request.method=='POST':
        try:
            if getattr(request,'limited',False):raise ValidationError('Too many attempts. Please wait five minutes and try again.')
            action=request.POST.get('action')
            if action=='confirm':
                if code_form.is_valid():
                    device,codes=confirm_enrollment(request.user,code_form.cleaned_data['token'],request.session.get('staff_enrollment_stamp',''))
                    if device:
                        request.session.pop('staff_enrollment_stamp',None)
                        request.session.cycle_key();otp_login(request,device)
                        messages.success(request,'Two-step sign-in is enabled. Save your recovery codes now.')
                    else:code_form.add_error('token','The code was not accepted. Wait for a fresh code and try again.')
            elif action in {'start','regenerate','disable'} and form.is_valid():
                if action=='start':
                    device=start_enrollment(request.user,form.cleaned_data['password'])
                    request.session.cycle_key()
                    request.session['staff_enrollment_stamp']=enrollment_stamp(device)
                else:
                    if action=='disable' and request.POST.get('confirm_disable')!='on':
                        raise ValidationError('Confirm that you want to disable two-step sign-in.')
                    valid,codes=change_factor(request.user,form.cleaned_data['password'],form.cleaned_data['token'],disable=action=='disable')
                    if not valid:form.add_error('token','The code was not accepted. Wait for a fresh code or use an unused recovery code.')
                    else:
                        request.session.cycle_key()
                        if action=='disable':
                            request.session.pop(DEVICE_ID_SESSION_KEY,None)
                            request.user.otp_device=None
                            messages.success(request,'Two-step sign-in was disabled for your account.')
                            return redirect('workqueue:staff_security')
                        messages.success(request,'New recovery codes created. Old recovery codes no longer work.')
                        otp_login(request,StaffOTPDevice.objects.get(user=request.user,confirmed=True))
            elif action not in {'start','regenerate','disable','confirm'}:
                raise ValidationError('Choose an available security action.')
        except ValidationError as exc:
            messages.error(request,' '.join(exc.messages))
    device=StaffOTPDevice.objects.filter(user=request.user).first()
    setup=None
    if (device and not device.confirmed and device.enrollment_expires_at>timezone.now()
            and request.session.get('staff_enrollment_stamp')==enrollment_stamp(device)):
        import segno
        setup={'qr':segno.make_qr(device.config_url).svg_data_uri(scale=5),
               'key':device.setup_key,'expires':device.enrollment_expires_at}
    return private_response(render(request,'workqueue/staff_security.html',{
        'device':device,'setup':setup,'form':form,'code_form':code_form,'recovery_codes':codes,
        'remaining_codes':device.recovery_codes.filter(used_at__isnull=True).count() if device and device.confirmed else 0,
    },status=429 if getattr(request,'limited',False) else 200))


class StaffResetForm(PasswordResetForm):
    def get_users(self,email):
        return (user for user in super().get_users(email) if user.is_staff)

    def send_mail(self,subject_template_name,email_template_name,context,from_email,to_email,html_email_template_name=None):
        from .models import NotificationDelivery
        import secrets
        subject=' '.join(render_to_string(subject_template_name,context).splitlines())
        NotificationDelivery.objects.create(recipient_kind='owner',recipient=to_email,subject=subject,
            body=render_to_string(email_template_name,context),provider_reference='staffreset-'+secrets.token_hex(16))


@method_decorator(never_cache,name='dispatch')
@method_decorator(ratelimit(key='ip',rate='10/h',method='POST',block=False),name='dispatch')
@method_decorator(ratelimit(key=email_key,rate='3/h',method='POST',block=False),name='dispatch')
class StaffPasswordResetView(PasswordResetView):
    form_class=StaffResetForm
    template_name='workqueue/staff_password_reset.html'
    email_template_name='workqueue/staff_reset_email.txt'
    subject_template_name='workqueue/staff_reset_subject.txt'
    success_url=reverse_lazy('workqueue:staff_password_reset_done')
    extra_context={'flow':'request'}

    def form_valid(self,form):
        if not getattr(self.request,'limited',False):
            from django.conf import settings
            from urllib.parse import urlsplit
            domain=urlsplit(getattr(settings,'INTAKE_PUBLIC_BASE_URL','')).netloc
            if not domain:raise ValidationError('The website address is not configured.')
            form.save(use_https=True,domain_override=domain,from_email=settings.DEFAULT_FROM_EMAIL,
                subject_template_name=self.subject_template_name,email_template_name=self.email_template_name,request=self.request)
        return redirect(self.success_url)


@method_decorator(never_cache,name='dispatch')
class StaffPasswordResetDoneView(PasswordResetDoneView):
    template_name='workqueue/staff_password_reset.html'
    extra_context={'flow':'sent'}


@method_decorator(never_cache,name='dispatch')
class StaffPasswordResetConfirmView(PasswordResetConfirmView):
    template_name='workqueue/staff_password_reset.html'
    success_url=reverse_lazy('workqueue:staff_password_reset_complete')
    extra_context={'flow':'confirm'}
    post_reset_login=False


@method_decorator(never_cache,name='dispatch')
class StaffPasswordResetCompleteView(PasswordResetCompleteView):
    template_name='workqueue/staff_password_reset.html'
    extra_context={'flow':'complete'}
