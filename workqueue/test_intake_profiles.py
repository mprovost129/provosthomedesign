import hashlib
import re
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client as Browser, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token
from .intake_profiles import COOKIE, CONTACT_FIELDS, profiles_for, remember_device
from .models import (Client, ClientContact, EmailAccessToken, IntakeDevice, IntakeDraft,
                     NotificationDelivery, ProjectAccess, WorkProject)
from .test_client_intake import new_answers


@override_settings(DEBUG=True, INTAKE_LOCAL_DEVELOPMENT=True, WORK_INTAKE_ENABLED=True, WORK_QUEUE_ENABLED=True,
    RECAPTCHA_ENTERPRISE_API_KEY='', RECAPTCHA_SECRET_KEY='', RECAPTCHA_PRIVATE_KEY='',
    INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com')
class IntakeProfileTests(TestCase):
    email = 'repeat@example.invalid'

    def setUp(self):
        cache.clear()
        self.account = Client.objects.create(name='Example Builders', normalized_name='example builders', kind='company',
            billing_street='PRIVATE 12 Billing Lane', billing_city='Rehoboth', billing_state='MA', billing_zip='02769',
            internal_notes='SECRET STAFF NOTES', pricing_category='contractor')
        self.contact = ClientContact.objects.create(client=self.account, email=self.email, full_name='Alex Private', phone='555-0100')
        ClientContact.objects.create(client=self.account, email='colleague@example.invalid', full_name='SECRET COLLEAGUE')
        self.url = reverse('workqueue:submit')
        self.return_url = reverse('workqueue:returning_client')

    def verify(self, *, remember=False, email=None, browser=None):
        record, raw = mint_email_token(email or self.email, destination='intake')
        response = (browser or self.client).post(reverse('workqueue:confirm_access'),
            {'token': raw, **({'remember_intake': 'on'} if remember else {})})
        return response, raw

    def recognized_browser(self):
        response, _ = self.verify(remember=True)
        browser = Browser()
        browser.cookies[COOKIE] = response.cookies[COOKIE].value
        return browser

    def test_anonymous_email_query_and_guessed_company_do_not_reveal_crm(self):
        response = self.client.get(self.url, {'email': self.email, 'contact': str(self.contact.pk)})
        self.assertEqual(response.context['intake_profiles'], [])
        self.assertNotContains(response, 'PRIVATE 12')
        self.assertNotContains(response, 'Alex Private')
        self.assertNotContains(response, self.email)
        self.assertContains(response, 'Remember the contact and billing details')
        self.assertIn('no-store', response['Cache-Control'])

    def test_returning_lookup_is_generic_for_known_unknown_and_rate_limited_addresses(self):
        first = self.client.post(self.return_url, {'email': self.email})
        second = self.client.post(self.return_url, {'email': 'unknown@example.invalid'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context['sent'], second.context['sent'])
        self.assertNotContains(first, 'PRIVATE 12')
        self.assertNotContains(first, 'Example Builders')
        self.assertContains(first, 'Check your email')
        delivery = NotificationDelivery.objects.get(recipient=self.email)
        self.assertIn('contact and billing details', delivery.body)
        self.assertNotIn('PRIVATE', delivery.body)
        self.assertEqual(EmailAccessToken.objects.get(email=self.email).destination, 'intake')
        for _ in range(6):
            response = self.client.post(self.return_url, {'email': self.email})
        self.assertEqual(NotificationDelivery.objects.filter(recipient=self.email).count(), 5)
        self.assertEqual(response.context['sent'], True)

    def test_lookup_checks_csrf_honeypot_and_recaptcha(self):
        csrf = Browser(enforce_csrf_checks=True)
        self.assertEqual(csrf.post(self.return_url, {'email': self.email}).status_code, 403)
        self.assertEqual(self.client.post(self.return_url, {'email': self.email, 'website': 'bot'}).status_code, 400)
        with override_settings(INTAKE_LOCAL_DEVELOPMENT=False):
            self.assertEqual(self.client.post(self.return_url, {'email': self.email}).status_code, 400)
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_verification_returns_to_intake_and_does_not_remember_without_consent(self):
        response, raw = self.verify()
        self.assertRedirects(response, self.url)
        self.assertFalse(IntakeDevice.objects.exists())
        self.assertEqual(response.cookies[COOKIE]['max-age'], 0)
        page = self.client.get(self.url)
        initial = page.context['form'].initial
        self.assertEqual(initial['contact_full_name'], 'Alex Private')
        self.assertEqual(initial['company'], 'Example Builders')
        self.assertEqual(initial['billing_street'], self.account.billing_street)
        self.assertNotIn('project_street', initial)
        self.assertNotIn('terms_accepted', initial)
        self.assertEqual(self.client.post(reverse('workqueue:confirm_access'), {'token': raw}).status_code, 400)
        self.assertNotContains(page, 'SECRET STAFF NOTES')
        self.assertNotContains(page, 'SECRET COLLEAGUE')
        self.assertNotContains(page, 'pricing_category')

    def test_token_get_is_inert_and_expired_verification_cannot_remember(self):
        record, raw = mint_email_token(self.email, destination='intake')
        page = self.client.get(reverse('workqueue:confirm_access'), {'token': raw})
        record.refresh_from_db()
        self.assertIsNone(record.used_at)
        self.assertNotContains(page, 'checked')
        self.assertFalse(IntakeDevice.objects.exists())
        record.expires_at = timezone.now() - timedelta(seconds=1)
        record.save()
        self.assertEqual(self.client.post(reverse('workqueue:confirm_access'), {'token': raw, 'remember_intake': 'on'}).status_code, 400)
        self.assertFalse(IntakeDevice.objects.exists())

    def test_remember_token_is_hashed_httponly_and_secure_in_production(self):
        request = RequestFactory().get(self.url)
        with override_settings(DEBUG=False):
            response = remember_device(request, HttpResponse(), self.email)
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie['secure']); self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['max-age'], 90 * 86400)
        self.assertEqual(cookie['domain'], '')
        record = IntakeDevice.objects.get()
        self.assertEqual(record.digest, hashlib.sha256(cookie.value.encode()).hexdigest())
        self.assertNotEqual(record.digest, cookie.value)
        self.assertAlmostEqual((record.expires_at - record.created_at).total_seconds(), 90 * 86400, delta=2)

    def test_recognized_device_prefills_without_session_but_does_not_grant_project_access(self):
        browser = self.recognized_browser()
        project = WorkProject.objects.create(name='SECRET PROJECT', canonical_reference='PHD-12345')
        ProjectAccess.objects.create(project=project, email=self.email)
        response = browser.get(self.url, {'kind': 'update'})
        self.assertEqual(response.context['form'].initial['contact_full_name'], 'Alex Private')
        self.assertEqual(response.context['client_email'], '')
        self.assertFalse(response.context['form'].fields['project'].queryset.exists())
        self.assertNotContains(response, 'SECRET PROJECT')
        self.assertNotContains(browser.get(reverse('workqueue:tracking')), 'SECRET PROJECT')
        self.assertFalse(browser.session.get('queue_verified_email'))

    def test_tampered_expired_revoked_or_other_device_cookies_reveal_nothing(self):
        browser = self.recognized_browser()
        record = IntakeDevice.objects.get()
        copied = browser.cookies[COOKIE].value
        for value in ['guess', 'a' * 150, copied + 'x']:
            browser.cookies[COOKIE] = value
            self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        browser.cookies[COOKIE] = copied
        record.expires_at = timezone.now() - timedelta(seconds=1); record.save()
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        record.expires_at = timezone.now() + timedelta(days=1); record.revoked_at = timezone.now(); record.save()
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        self.assertEqual(Browser().get(self.url).context['intake_profiles'], [])

    def test_changed_archived_or_merged_crm_association_is_checked_again(self):
        browser = self.recognized_browser()
        self.contact.email = 'reassigned@example.invalid'; self.contact.save()
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        self.contact.email = self.email; self.contact.archived_at = timezone.now(); self.contact.save()
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        self.contact.archived_at = None; self.contact.save()
        replacement = Client.objects.create(name='Different account', normalized_name='different')
        self.account.merged_into = replacement; self.account.save()
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])

    def test_multiple_companies_require_choice_and_only_exact_contact_is_projected(self):
        other = Client.objects.create(name='Second Company', normalized_name='second', kind='company', billing_street='Second Billing')
        ClientContact.objects.create(client=other, email=self.email.upper(), full_name='Same mailbox', phone='SECOND PHONE')
        ClientContact.objects.create(client=other, email='another@example.invalid', full_name='SECRET OTHER PERSON')
        self.verify()
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['intake_profiles']), 2)
        self.assertNotIn('company', response.context['form'].initial)
        self.assertContains(response, 'Choose your contact / company')
        self.assertNotContains(response, 'SECRET OTHER PERSON')
        for profile in response.context['intake_profiles']:
            self.assertEqual(set(profile['values']), set(CONTACT_FIELDS))
        self.assertFalse(ProjectAccess.objects.exists())

    def test_failed_validation_and_draft_answers_override_crm_defaults(self):
        self.verify()
        page = self.client.get(self.url)
        token = page.context['form'].initial['intake_token']
        response = self.client.post(self.url, new_answers(intake_token=token, contact_full_name='Edited name', billing_street='Edited address', new_description=''))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['form']['contact_full_name'].value(), 'Edited name')
        self.assertEqual(response.context['form']['billing_street'].value(), 'Edited address')
        self.assertFalse(response.context['can_restore_details'])
        from .access import read_intake_token
        from .drafts import save_draft
        request = page.wsgi_request
        nonce = read_intake_token(request, token)
        draft = save_draft(request=request, nonce=nonce, payload={'contact_full_name': 'Draft name', 'billing_street': '', 'kind': 'new'}, revision=0)
        response = self.client.get(self.url, {'resume': str(draft.pk)})
        self.assertEqual(response.context['form']['contact_full_name'].value(), 'Draft name')
        self.assertEqual(response.context['form']['billing_street'].value(), '')
        self.assertFalse(response.context['can_restore_details'])
        self.account.refresh_from_db(); self.contact.refresh_from_db()
        self.assertEqual(self.contact.full_name, 'Alex Private')
        self.assertEqual(self.account.billing_street, 'PRIVATE 12 Billing Lane')

    def test_update_form_does_not_silently_include_hidden_crm_billing_defaults(self):
        self.verify()
        page = self.client.get(self.url, {'kind': 'update'})
        form = page.context['form']
        self.assertFalse(form.initial.get('billing_street'))
        from .client_forms import ClientIntakeForm
        bound = ClientIntakeForm(new_answers(kind='update', intake_token=form.initial['intake_token'],
            project_context='My project', update_description='Revised plans', categories=['Revision request']),
            initial={'billing_street': self.account.billing_street}, email=self.email)
        self.assertTrue(bound.is_valid(), bound.errors)
        self.assertEqual(bound.cleaned_data['billing_street'], '')

    def test_forget_revokes_device_clears_browser_drafts_and_prevents_replay(self):
        browser = self.recognized_browser()
        raw = browser.cookies[COOKIE].value
        page = browser.get(self.url)
        request = page.wsgi_request
        from .access import read_intake_token
        from .drafts import save_draft
        nonce = read_intake_token(request, page.context['form'].initial['intake_token'])
        save_draft(request=request, nonce=nonce, payload={'contact_full_name': 'Unfinished'}, revision=0)
        response = browser.post(reverse('workqueue:forget_intake'))
        self.assertRedirects(response, self.url)
        self.assertFalse(IntakeDraft.objects.exists())
        self.assertIsNotNone(IntakeDevice.objects.get().revoked_at)
        browser.cookies[COOKIE] = raw
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        self.assertFalse(browser.session.get('queue_verified_email'))

    def test_signout_revokes_device_and_new_identity_never_inherits_old_recognition(self):
        browser = self.recognized_browser()
        self.verify(email='someoneelse@example.invalid', browser=browser)
        self.assertTrue(IntakeDevice.objects.exclude(revoked_at=None).exists())
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])
        self.verify(remember=True, browser=browser)
        browser.post(reverse('workqueue:client_logout'))
        self.assertFalse(IntakeDevice.objects.filter(revoked_at=None).exists())
        self.assertEqual(browser.get(self.url).context['intake_profiles'], [])

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_intake_feature_flag_blocks_new_endpoints(self):
        self.assertEqual(self.client.get(self.return_url).status_code, 404)
        self.assertEqual(self.client.post(reverse('workqueue:forget_intake')).status_code, 404)
