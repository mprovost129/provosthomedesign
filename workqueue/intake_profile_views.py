from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import normalize_email
from .client_security import verify_form
from .client_views import intake_enabled, limited
from .notifications import queue_signin


class ReturningClientForm(forms.Form):
    email = forms.EmailField(label='Email address', widget=forms.EmailInput(attrs={'autocomplete': 'email', 'class': 'input'}))
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('This request could not be accepted.')
        return ''


@intake_enabled
@never_cache
@require_http_methods(['GET', 'POST'])
def returning_client(request):
    from .intake_profiles import intake_identity
    form = ReturningClientForm(request.POST or None, initial={'email': intake_identity(request)})
    sent = False
    status = 200
    if request.method == 'POST' and verify_form(request, form, 'work_intake_signin'):
        email = normalize_email(form.cleaned_data['email'])
        # Neither response nor mail content reveals whether an address is in CRM.
        if limited(request, 'intake-signin-ip', limit=20, window=900) and limited(request, 'intake-signin-email', email, limit=5, window=900):
            queue_signin(email, destination='intake')
        sent = True
        form = ReturningClientForm()
    elif request.method == 'POST':
        status = 400
    return render(request, 'workqueue/returning_client.html', {'form': form, 'sent': sent}, status=status)


@intake_enabled
@never_cache
@require_http_methods(['POST'])
def forget_intake(request):
    from .access import session_digest
    from .intake_profiles import forget_cookie, revoke_device
    from .models import IntakeDraft
    revoke_device(request)
    IntakeDraft.objects.filter(session_digest=session_digest(request)).delete()
    for key in ('queue_verified_email', 'queue_email_verified_until', 'queue_recent_requests', 'queue_intake_binding'):
        request.session.pop(key, None)
    request.session.cycle_key()
    messages.success(request, 'Saved intake details and unfinished forms have been forgotten on this device. Submitted work is unchanged.')
    return forget_cookie(redirect('workqueue:submit'))
