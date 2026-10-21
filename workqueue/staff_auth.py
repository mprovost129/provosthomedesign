"""Staff sign-in uses Django's existing users, sessions and CSRF protection."""
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit


def can_enter(user):
    return (user.is_authenticated and user.is_active and user.is_staff
            and user.has_perm('workqueue.view_workitem'))


class StaffAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not can_enter(user):
            raise ValidationError(self.error_messages['invalid_login'],
                                  code='invalid_login', params={'username': self.username_field.verbose_name})


@method_decorator(never_cache, name='dispatch')
class StaffLoginView(LoginView):
    template_name = 'workqueue/staff_login.html'
    authentication_form = StaffAuthenticationForm
    next_page = reverse_lazy('workqueue:dashboard')
    http_method_names = ['get', 'post', 'head', 'options']

    def get_redirect_url(self):
        # Django rejects external and insecure redirect targets. Avoid a loop
        # if someone bookmarks a sign-in URL pointing at sign-in/sign-out.
        from urllib.parse import urlsplit
        target = super().get_redirect_url()
        if urlsplit(target).path in {'/staff/login/', '/staff/logout/'}:
            return ''
        return target

    def get(self, request, *args, **kwargs):
        if can_enter(request.user):
            return redirect(self.get_success_url())
        return super().get(request, *args, **kwargs)

    @method_decorator(ratelimit(key='ip', rate='30/5m', method='POST', block=False))
    @method_decorator(ratelimit(key='post:username', rate='10/5m', method='POST', block=False))
    def post(self, request, *args, **kwargs):
        if getattr(request, 'limited', False):
            # Do not authenticate or hash a password after the limit is reached.
            form = self.authentication_form(request=request)
            response = self.render_to_response(self.get_context_data(
                form=form, sign_in_limited=True), status=429)
            response['Retry-After'] = '300'
            return response
        return super().post(request, *args, **kwargs)


@method_decorator(never_cache, name='dispatch')
class StaffLogoutView(LogoutView):
    # Sign-out is a CSRF-protected POST; visiting a link never ends a session.
    next_page = reverse_lazy('workqueue:staff_login')
