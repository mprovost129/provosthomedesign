import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection,close_old_connections
from django.test import Client,TestCase,TransactionTestCase,override_settings,skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from .models import NotificationDelivery
from .staff_security import (start_enrollment,confirm_enrollment,enrollment_stamp,
    verify_staff_token,make_recovery_codes,change_factor,safe_next)
from .staff_security_models import StaffOTPDevice,StaffRecoveryCode


def token_for(device,offset=0):
    clock=TOTP(bytes.fromhex(device.key),step=30,digits=6)
    clock.time=time.time()+offset
    return f'{clock.token():06d}'


@override_settings(INTAKE_PUBLIC_BASE_URL='https://www.provosthomedesign.com')
class StaffSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner=get_user_model().objects.create_superuser('security-owner','owner@example.invalid','current-password')
        self.security=reverse('workqueue:staff_security');self.verify=reverse('workqueue:staff_mfa_verify')

    def tearDown(self):cache.clear()

    def enrolled(self):
        return StaffOTPDevice.objects.create(user=self.owner,name='Test authenticator',confirmed=True)

    def test_existing_staff_access_is_preserved_until_enrollment(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/dashboard/').status_code,200)
        response=self.client.get(self.security)
        self.assertContains(response,'Start authenticator setup')
        self.assertFalse(StaffOTPDevice.objects.exists())
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'],'no-referrer')

    def test_enrollment_requires_password_and_never_replaces_confirmed_device(self):
        with self.assertRaises(ValidationError):start_enrollment(self.owner,'wrong')
        device=start_enrollment(self.owner,'current-password')
        self.assertFalse(device.confirmed)
        self.assertEqual(len(device.key),40)
        device.confirmed=True;device.save(update_fields=['confirmed'])
        with self.assertRaises(ValidationError):start_enrollment(self.owner,'current-password')
        self.assertEqual(StaffOTPDevice.objects.get().key,device.key)

    def test_setup_key_is_encrypted_in_database(self):
        device=self.enrolled()
        with connection.cursor() as cursor:
            cursor.execute('SELECT key FROM workqueue_staffotpdevice WHERE id=%s',[device.pk])
            encrypted=cursor.fetchone()[0]
        self.assertNotEqual(encrypted,device.key)
        self.assertNotIn(device.key,encrypted)
        self.assertEqual(StaffOTPDevice.objects.get(pk=device.pk).key,device.key)

    def test_secret_key_rotation_can_read_fallback_encryption(self):
        device=self.enrolled()
        from django.conf import settings
        original=settings.SECRET_KEY
        with override_settings(SECRET_KEY='new-fake-test-key',SECRET_KEY_FALLBACKS=[original]):
            self.assertEqual(StaffOTPDevice.objects.get(pk=device.pk).key,device.key)

    def test_setup_requires_owner_session_stamp_and_unexpired_device(self):
        device=start_enrollment(self.owner,'current-password')
        with self.assertRaises(ValidationError):confirm_enrollment(self.owner,token_for(device),'wrong-stamp')
        StaffOTPDevice.objects.filter(pk=device.pk).update(enrollment_expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):confirm_enrollment(self.owner,token_for(device),enrollment_stamp(device))
        self.assertFalse(StaffOTPDevice.objects.get().confirmed)

    def test_confirm_enrollment_creates_only_hashed_recovery_codes_and_security_notice(self):
        device=start_enrollment(self.owner,'current-password')
        confirmed,codes=confirm_enrollment(self.owner,token_for(device),enrollment_stamp(device))
        self.assertTrue(confirmed.confirmed);self.assertEqual(len(codes),10)
        stored=str(list(StaffRecoveryCode.objects.values()))
        notice=NotificationDelivery.objects.get()
        for code in codes:
            self.assertNotIn(code,stored);self.assertNotIn(code,notice.body)
        self.assertNotIn(device.key,notice.body)

    def test_totp_is_single_use_and_rejects_old_and_invalid_codes(self):
        device=self.enrolled();code=token_for(device)
        self.assertIsNotNone(verify_staff_token(self.owner,code))
        self.assertIsNone(verify_staff_token(self.owner,code))
        device.refresh_from_db()
        self.assertGreater(device.throttling_failure_count,0)
        self.assertIsNone(verify_staff_token(self.owner,token_for(device,120)))

    def test_recovery_code_is_single_use_and_cannot_cross_accounts(self):
        device=self.enrolled();codes=make_recovery_codes(device)
        self.assertIsNotNone(verify_staff_token(self.owner,codes[0]))
        self.assertIsNone(verify_staff_token(self.owner,codes[0]))
        other=get_user_model().objects.create_user('other-staff',is_staff=True)
        StaffOTPDevice.objects.create(user=other,confirmed=True)
        self.assertIsNone(verify_staff_token(other,codes[1]))

    def test_enrolled_sessions_are_blocked_on_queue_admin_and_private_file_urls(self):
        self.enrolled();self.client.force_login(self.owner)
        for path in ['/dashboard/','/work-queue/','/clients/','/admin/',
                     '/work-files/00000000-0000-0000-0000-000000000001/']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,302,path)
            self.assertIn('/staff/verify/',response.url)
        self.assertEqual(self.client.get(self.verify).status_code,200)

    def test_successful_verification_rotates_session_and_returns_to_safe_destination(self):
        device=self.enrolled();self.client.force_login(self.owner)
        previous=self.client.session.session_key
        response=self.client.post(self.verify,{'token':token_for(device),'next':'/work-queue/?scope=all'})
        self.assertEqual(response.url,'/work-queue/?scope=all')
        self.assertNotEqual(previous,self.client.session.session_key)
        self.assertEqual(self.client.get('/dashboard/').status_code,200)

    def test_mfa_next_rejects_external_auth_and_public_destinations(self):
        device=self.enrolled();self.client.force_login(self.owner)
        response=self.client.post(self.verify,{'token':token_for(device),'next':'https://evil.example'})
        self.assertEqual(response.url,'/dashboard/')

    def test_oauth_query_secrets_are_not_copied_to_mfa_redirect(self):
        self.enrolled();self.client.force_login(self.owner)
        response=self.client.get('/work-projects/billing/callback/?code=PRIVATE_CODE&state=PRIVATE_STATE')
        self.assertIn('/staff/verify/',response.url)
        self.assertNotIn('PRIVATE',response.url)

    def test_another_device_cannot_mark_owner_verified(self):
        own=self.enrolled()
        other=get_user_model().objects.create_user('different-staff',is_staff=True)
        stolen=StaffOTPDevice.objects.create(user=other,confirmed=True)
        self.client.force_login(self.owner)
        session=self.client.session;session[DEVICE_ID_SESSION_KEY]=stolen.persistent_id;session.save()
        self.assertIn('/staff/verify/',self.client.get('/dashboard/').url)
        self.assertNotIn(DEVICE_ID_SESSION_KEY,self.client.session)

    def test_csrf_and_anonymous_access_are_protected(self):
        self.assertIn('/staff/login/',self.client.get(self.security).url)
        browser=Client(enforce_csrf_checks=True);browser.force_login(self.owner)
        for path in [self.security,self.verify]:
            self.assertEqual(browser.post(path,{'action':'start','password':'current-password'}).status_code,403)
        self.assertFalse(StaffOTPDevice.objects.exists())

    def test_ui_start_confirms_codes_once_without_changing_other_users(self):
        self.client.force_login(self.owner)
        self.client.post(self.security,{'action':'start','password':'current-password'})
        device=StaffOTPDevice.objects.get()
        response=self.client.post(self.security,{'action':'confirm','token':token_for(device)})
        codes=response.context['recovery_codes']
        self.assertEqual(len(codes),10)
        for code in codes:self.assertContains(response,code)
        later=self.client.get(self.security)
        for code in codes:self.assertNotContains(later,code)
        self.assertNotContains(later,device.key)
        self.assertNotContains(later,'Authenticator setup QR code')

    def test_pending_qr_is_visible_only_in_enrolling_session(self):
        self.client.force_login(self.owner)
        self.client.post(self.security,{'action':'start','password':'current-password'})
        self.assertContains(self.client.get(self.security),'Authenticator setup QR code')
        different=Client();different.force_login(self.owner)
        self.assertNotContains(different.get(self.security),'Authenticator setup QR code')

    def test_recovery_regeneration_requires_password_and_code_and_invalidates_previous_codes(self):
        device=self.enrolled();old=make_recovery_codes(device)
        with self.assertRaises(ValidationError):change_factor(self.owner,'bad',old[0])
        valid,codes=change_factor(self.owner,'current-password',old[0])
        self.assertTrue(valid);self.assertEqual(len(codes),10)
        self.assertIsNone(verify_staff_token(self.owner,old[1]))

    def test_disable_requires_confirmation_password_and_code(self):
        device=self.enrolled();codes=make_recovery_codes(device);self.client.force_login(self.owner)
        self.client.post(self.security,{'action':'disable','password':'current-password','token':codes[0]})
        self.assertTrue(StaffOTPDevice.objects.exists())
        self.client.post(self.security,{'action':'disable','password':'current-password','token':codes[0],'confirm_disable':'on'})
        self.assertFalse(StaffOTPDevice.objects.exists())
        self.assertFalse(StaffRecoveryCode.objects.exists())

    def test_security_pages_do_not_enable_a_nonstaff_client_account(self):
        client=get_user_model().objects.create_user('website-client',password='test')
        self.client.force_login(client)
        self.assertIn('/staff/login/',self.client.get(self.security).url)
        self.assertFalse(StaffOTPDevice.objects.exists())

    def test_password_reset_is_staff_only_generic_and_queued_without_sending(self):
        get_user_model().objects.create_user('client',email='client@example.invalid',password='test')
        reset=reverse('workqueue:staff_password_reset')
        for email in ['owner@example.invalid','client@example.invalid','missing@example.invalid']:
            self.assertEqual(self.client.post(reset,{'email':email}).url,reverse('workqueue:staff_password_reset_done'))
        notice=NotificationDelivery.objects.get()
        self.assertEqual(notice.recipient,'owner@example.invalid')
        self.assertEqual(notice.state,'pending')
        self.assertIn('https://www.provosthomedesign.com/staff/reset/',notice.body)
        self.assertNotIn('testserver',notice.body)

    def test_reset_rate_limit_normalizes_email_case(self):
        reset=reverse('workqueue:staff_password_reset')
        for _ in range(4):self.client.post(reset,{'email':'OWNER@example.invalid'})
        self.assertEqual(NotificationDelivery.objects.count(),3)

    def test_password_reset_token_expires_and_is_single_use(self):
        uid=urlsafe_base64_encode(force_bytes(self.owner.pk))
        with patch.object(default_token_generator,'_now',return_value=datetime.now()-timedelta(hours=2)):
            expired=default_token_generator.make_token(self.owner)
        response=self.client.get(reverse('workqueue:staff_password_reset_confirm',kwargs={'uidb64':uid,'token':expired}))
        self.assertFalse(response.context['validlink'])
        self.enrolled()
        token=default_token_generator.make_token(self.owner)
        link=reverse('workqueue:staff_password_reset_confirm',kwargs={'uidb64':uid,'token':token})
        self.client.get(link,follow=True)
        set_url=reverse('workqueue:staff_password_reset_confirm',kwargs={'uidb64':uid,'token':'set-password'})
        response=self.client.post(set_url,{'new_password1':'New-password-example!','new_password2':'New-password-example!'})
        self.assertEqual(response.url,reverse('workqueue:staff_password_reset_complete'))
        self.owner.refresh_from_db();self.assertTrue(self.owner.check_password('New-password-example!'))
        self.assertTrue(StaffOTPDevice.objects.get().confirmed)
        self.assertFalse(default_token_generator.check_token(self.owner,token))
        self.client.post(reverse('workqueue:staff_login'),{'username':self.owner.username,'password':'New-password-example!'})
        self.assertIn('/staff/verify/',self.client.get('/dashboard/').url)


class StaffOTPConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_totp_is_accepted_once_across_parallel_sessions(self):
        owner=get_user_model().objects.create_user('concurrent-otp',is_staff=True)
        device=StaffOTPDevice.objects.create(user=owner,confirmed=True)
        token=token_for(device)
        def verify(_):
            close_old_connections()
            try:return bool(verify_staff_token(owner,token))
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(verify,range(4)))
        self.assertEqual(sum(results),1)
