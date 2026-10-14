import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect,render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .access import new_intake_token,read_intake_token,session_digest,verified_email,normalize_email
from .booking import eligible
from .booking_views import booking_enabled
from .client_views import limited
from .client_security import verify_form
from .consultations import submit_consultation,resend_verification
from .models import ConsultationRequest


class ConsultationForm(forms.Form):
    token=forms.CharField(widget=forms.HiddenInput)
    full_name=forms.CharField(label='Full name',max_length=200)
    email=forms.EmailField(label='Email address',widget=forms.EmailInput(attrs={'autocomplete':'email'}))
    phone=forms.CharField(label='Phone number',max_length=50)
    description=forms.CharField(label='Tell us briefly about your new project',max_length=3000,widget=forms.Textarea(attrs={'rows':4}))
    terms_accepted=forms.BooleanField(required=True,label='Terms accepted',error_messages={'required':'Please agree to the Terms & Conditions before requesting a consultation.'})


@booking_enabled
@never_cache
@require_http_methods(['GET','POST'])
def consultation(request):
    email=verified_email(request)
    latest=ConsultationRequest.objects.filter(email=email).order_by('-created_at').first() if email else None
    if eligible(email):
        return redirect('workqueue:book')
    sent=request.session.pop('consultation_verification_sent',False)
    form=ConsultationForm(request.POST or None,initial={'token':new_intake_token(request),'email':email})
    status=200
    if request.method=='POST' and verify_form(request,form,'work_consultation'):
        try:
            nonce=read_intake_token(request,form.cleaned_data['token'])
            target=normalize_email(form.cleaned_data['email'])
            if not limited(request,'consultation-ip',limit=10,window=3600) or not limited(request,'consultation-email',target,limit=3,window=3600):
                raise ValidationError('Too many requests. Please try again later.')
            item=submit_consultation(email=target,full_name=form.cleaned_data['full_name'],phone=form.cleaned_data['phone'],
                description=form.cleaned_data['description'],key='consultation:'+session_digest(request)+':'+str(nonce),
                verified=bool(email and email==target),terms_url=reverse('pages:terms'))
            if item.state=='unverified':
                request.session['consultation_verification_sent']=True
                request.session['consultation_recent_id']=str(item.pk)
                request.session['consultation_recent_email']=target
            messages.success(request,'Your consultation request was saved. Verify your email before Mike reviews it.' if item.state=='unverified' else 'Your consultation request is ready for Mike to review.')
            return redirect('workqueue:consultation')
        except ValidationError as exc:
            form.add_error(None,exc)
    elif request.method=='POST':
        status=400
    if form.errors:status=400
    return render(request,'workqueue/consultation.html',{'form':form,'client_email':email,'consultation':latest,'sent':sent,
        'resend_token':new_intake_token(request),'can_resend':ConsultationRequest.objects.filter(
            pk=request.session.get('consultation_recent_id'),email=request.session.get('consultation_recent_email',''),
            state='unverified').exists() if request.session.get('consultation_recent_id') else False},status=status)


@booking_enabled
@never_cache
@require_http_methods(['POST'])
def resend(request):
    form=forms.Form(request.POST)
    form.fields['token']=forms.CharField(widget=forms.HiddenInput)
    if verify_form(request,form,'work_consultation'):
        try:
            read_intake_token(request,form.cleaned_data['token'])
            email=request.session.get('consultation_recent_email','')
            request_id=uuid.UUID(request.session.get('consultation_recent_id',''))
            if not limited(request,'consultation-resend-ip',limit=5,window=3600) or not limited(request,'consultation-resend-email',email,limit=3,window=3600):
                raise ValidationError('Please wait before requesting another verification email.')
            resend_verification(request_id,email)
            request.session['consultation_verification_sent']=True
            messages.success(request,'Check your email for a new consultation verification link.')
        except (ValueError,ConsultationRequest.DoesNotExist):
            raise Http404
        except ValidationError as exc:
            messages.error(request,' '.join(exc.messages))
    else:
        messages.error(request,'Please try the verification request again.')
    return redirect('workqueue:consultation')
