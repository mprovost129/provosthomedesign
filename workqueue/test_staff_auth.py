from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class StaffAuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.login = reverse('workqueue:staff_login')
        self.logout = reverse('workqueue:staff_logout')
        self.dashboard = reverse('workqueue:dashboard')
        self.owner = get_user_model().objects.create_superuser('staff-owner', '', 'test-password')

    def tearDown(self):
        cache.clear()

    def sign_in(self, **extra):
        return self.client.post(self.login, {'username':'staff-owner', 'password':'test-password', **extra})

    def test_login_page_is_public_private_cache_policy_and_accessible(self):
        response = self.client.get(self.login)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Welcome back')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'noindex, nofollow')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotContains(response, 'aria-label="Staff workspace"')

    def test_existing_credentials_open_dashboard_and_rotate_session(self):
        session = self.client.session
        session['before_login'] = True
        session.save()
        previous_key = session.session_key
        self.assertRedirects(self.sign_in(), self.dashboard, fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.owner.pk))
        self.assertNotEqual(previous_key, self.client.session.session_key)

    def test_valid_next_preserves_filter_and_fragment(self):
        target = '/work-queue/?scope=all&attention=overdue#appointments'
        self.assertRedirects(self.sign_in(next=target), target, fetch_redirect_response=False)

    def test_external_and_loop_redirects_fall_back_to_dashboard(self):
        for target in ['https://evil.example/steal', '//evil.example/', '/staff/login/?next=/staff/login/',
                       '/staff/logout/', 'javascript:alert(1)']:
            with self.subTest(target=target):
                self.assertRedirects(self.sign_in(next=target), self.dashboard, fetch_redirect_response=False)

    def test_secure_login_rejects_http_next(self):
        response = self.client.post(self.login, {'username':'staff-owner','password':'test-password',
            'next':'http://testserver/work-queue/'}, secure=True)
        self.assertEqual(response.url, self.dashboard)

    def test_authenticated_staff_skip_login_form(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(self.login), self.dashboard, fetch_redirect_response=False)
        self.assertRedirects(self.client.get(self.login, {'next':'/clients/'}), '/clients/', fetch_redirect_response=False)

    def test_client_disabled_and_unpermitted_staff_cannot_sign_in(self):
        for name, flags in [('client',{}),('disabled',{'is_staff':True,'is_active':False}),
                            ('unpermitted',{'is_staff':True})]:
            user = get_user_model().objects.create_user(name, password='test-password', **flags)
            response = self.client.post(self.login, {'username':name,'password':'test-password'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(SESSION_KEY, self.client.session)
            self.assertContains(response, 'Please enter a correct')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.login).status_code, 200)
        self.assertEqual(self.client.get(self.dashboard).status_code, 403)

    def test_staff_with_existing_queue_permission_can_sign_in(self):
        user=get_user_model().objects.create_user('staff-viewer', password='test-password', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        response=self.client.post(self.login, {'username':user.username,'password':'test-password'})
        self.assertRedirects(response, self.dashboard, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)

    def test_incorrect_credentials_never_create_session(self):
        response = self.client.post(self.login, {'username':'staff-owner','password':'incorrect'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(response, 'value="incorrect"')

    def test_csrf_required_for_login_and_logout_and_logout_is_post_only(self):
        browser=Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post(self.login, {'username':'staff-owner','password':'test-password'}).status_code,403)
        browser.get(self.login)
        token=browser.cookies['csrftoken'].value
        self.assertEqual(browser.post(self.login, {'username':'staff-owner','password':'test-password',
            'csrfmiddlewaretoken':token}).status_code,302)
        self.assertEqual(browser.get(self.logout).status_code,405)
        self.assertEqual(browser.post(self.logout).status_code,403)
        token=browser.cookies['csrftoken'].value
        response=browser.post(self.logout, {'csrfmiddlewaretoken':token})
        self.assertEqual(response.url,self.login)
        self.assertNotIn(SESSION_KEY,browser.session)
        self.assertIn('/staff/login/',browser.get(self.dashboard).url)

    def test_rate_limit_does_not_authenticate_limited_request(self):
        # Keep the attempts in one rate-limit window, even on slower runners.
        # Patch the limiter's module reference, not Python's global clock or
        # the actual rate-limit decision. Cache expiry keeps using real time.
        with patch('django_ratelimit.core.time') as rate_clock:
            rate_clock.time.return_value = 1_800_000_100
            for _ in range(10):
                self.client.post(self.login,{'username':'staff-owner','password':'wrong'})
            with patch('django.contrib.auth.forms.authenticate', return_value=None) as authenticate:
                response=self.sign_in()
        self.assertEqual(response.status_code,429)
        authenticate.assert_not_called()
        self.assertEqual(response['Retry-After'],'300')
        self.assertContains(response,'wait five minutes',status_code=429)
        self.assertIn('no-store',response['Cache-Control'])

    def test_rate_limit_allows_an_attempt_after_the_window_resets(self):
        with patch('django_ratelimit.core.time') as rate_clock:
            rate_clock.time.return_value = 1_800_000_100
            for _ in range(11):
                response=self.client.post(self.login,{'username':'staff-owner','password':'wrong'})
            self.assertEqual(response.status_code,429)
            rate_clock.time.return_value += 301
            with patch('django.contrib.auth.forms.authenticate', return_value=None) as authenticate:
                response=self.client.post(self.login,{'username':'staff-owner','password':'wrong'})
            self.assertEqual(response.status_code,200)
            authenticate.assert_called_once()
            self.assertNotIn(SESSION_KEY,self.client.session)

    def test_staff_routes_use_new_login_with_original_destination(self):
        for target in ['/dashboard/','/dashboard/filing/','/clients/','/clients/import/',
                       '/work-projects/','/work-projects/invoices/','/work-queue/?scope=all']:
            response=self.client.get(target)
            self.assertEqual(response.status_code,302,target)
            redirect=urlsplit(response.url)
            self.assertEqual(redirect.path,self.login)
            self.assertEqual(parse_qs(redirect.query)['next'],[target])

    def test_shared_navigation_and_signout_on_main_staff_pages(self):
        self.client.force_login(self.owner)
        for target in ['/dashboard/','/clients/','/work-projects/','/work-queue/','/clients/new/']:
            response=self.client.get(target)
            self.assertEqual(response.status_code,200,target)
            self.assertContains(response,'aria-label="Staff workspace"',count=1)
            self.assertContains(response,'action="/staff/logout/"',count=1)
            self.assertContains(response,'Signed in as')
        self.assertContains(self.client.get('/dashboard/'),'href="/dashboard/" aria-current="page"')

    def test_public_pages_offer_staff_login_without_staff_navigation(self):
        response=self.client.get('/track-work/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'href="/staff/login/"')
        self.assertNotContains(response,'aria-label="Staff workspace"')

    def test_administration_and_client_access_routes_remain_unchanged(self):
        self.assertEqual(self.client.get('/admin/login/').status_code,200)
        response=self.client.get('/client-portal/')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'aria-label="Staff workspace"')
