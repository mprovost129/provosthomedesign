import copy,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth.models import Permission
from django.core import mail,signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,close_old_connections
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .test_client_payments import ClientPaymentFixtures,SETTINGS
from .models import NotificationDelivery
from .billing_mail_models import NativeBillingMailSettings,NativeReceiptEmail,NativeBillingMailEvent
from .billing_mail import (current_settings,save_settings,queue_receipt,queue_due_receipts,delivery_action,
    cancel_ineligible_receipt_notice,receipt_attachment,ReceiptDocumentError)
from .billing_mail_views import SALT
from .native_invoices import balance
from .notifications import deliver_pending
from .services import EditConflict


class MailFixtures(ClientPaymentFixtures):
    def seed_mail(self):
        self.seed_client_payment();self.attempt=self.paid(self.checkout());self.receipt=self.attempt.test_receipt
        NotificationDelivery.objects.update(state='canceled',body='',html_body='')
        self.client.force_login(self.actor)

    def queue_mail(self,**changes):
        return queue_receipt(receipt_id=self.receipt.pk,actor=self.actor,**changes)

    def enable(self):return save_settings(actor=self.actor,version=current_settings().version,automatic_receipts=True)

    def action(self,delivery,action,**changes):
        delivery.notification.refresh_from_db();notice=delivery.notification
        args=dict(delivery_id=delivery.pk,actor=self.actor,action=action,expected_state=notice.state,attempts=notice.attempts)
        args.update(changes);return delivery_action(**args)


@override_settings(**SETTINGS)
class BillingMailTests(MailFixtures,TestCase):
    def setUp(self):self.seed_mail()

    def test_default_off_and_existing_payments_are_not_backfilled(self):
        self.assertFalse(current_settings().automatic_receipts)
        self.assertEqual(queue_due_receipts(),0)
        self.enable();self.assertEqual(queue_due_receipts(),0)
        self.assertFalse(NativeReceiptEmail.objects.exists())

    def test_future_verified_receipt_is_queued_once(self):
        self.enable()
        # Move only the fixture's timestamp across the activation boundary.
        self.receipt.created_at=timezone.now();self.receipt.save()
        self.assertEqual(queue_due_receipts(),1);self.assertEqual(queue_due_receipts(),0)
        delivery=NativeReceiptEmail.objects.get();self.assertTrue(delivery.automatic)
        self.assertIsNone(delivery.actor)

    def test_reenable_does_not_send_old_automatic_receipts(self):
        config=self.enable();self.receipt.created_at=timezone.now();self.receipt.save();queue_due_receipts()
        delivery=NativeReceiptEmail.objects.get()
        save_settings(actor=self.actor,version=config.version,automatic_receipts=False)
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'canceled')
        self.enable();self.assertEqual(queue_due_receipts(),0)

    def test_manual_review_is_owner_only_and_idempotent(self):
        delivery=self.queue_mail();self.assertEqual(self.queue_mail().pk,delivery.pk)
        self.assertEqual(NativeReceiptEmail.objects.count(),1)
        self.assertEqual(delivery.notification.recipient,'owner@example.invalid')
        self.assertEqual(delivery.notification.to_recipients,[])
        self.assertIn('[TEST ONLY]',delivery.notification.subject)
        self.assertNotIn('pi_',delivery.notification.body)
        self.assertNotIn('Internal accounting',delivery.notification.body)

    def test_mail_uses_frozen_receipt_and_does_not_change_accounting(self):
        before=balance(self.bill);snapshot=copy.deepcopy(self.receipt.snapshot)
        self.bill.snapshot['client_name']='Changed later';self.bill.save()
        delivery=self.queue_mail();self.assertNotIn('Changed later',delivery.notification.body)
        self.assertEqual(balance(self.bill),before);self.receipt.refresh_from_db();self.assertEqual(self.receipt.snapshot,snapshot)

    def test_plain_text_preserves_names_and_html_escapes_them(self):
        self.receipt.snapshot['client_name']='A&B <Client>';self.receipt.save()
        delivery=self.queue_mail()
        self.assertIn('A&B <Client>',delivery.notification.body)
        self.assertIn('A&amp;B &lt;Client&gt;',delivery.notification.html_body)

    def test_pause_cancels_automatic_waiting_mail_but_preserves_manual(self):
        config=self.enable();delivery=self.queue_mail()
        save_settings(actor=self.actor,version=config.version,automatic_receipts=False)
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'pending')

    def test_settings_optimistic_version(self):
        self.enable()
        with self.assertRaises(EditConflict):save_settings(actor=self.actor,version=1,automatic_receipts=False)
        self.assertTrue(current_settings().automatic_receipts)

    def test_non_owner_cannot_send_change_settings_or_resolve(self):
        delivery=self.queue_mail();self.actor.is_superuser=False;self.actor.save()
        self.actor.user_permissions.add(*Permission.objects.filter(codename__in=['view_workitem','change_workitem']))
        with self.assertRaises(PermissionDenied):self.queue_mail()
        with self.assertRaises(PermissionDenied):self.enable()
        with self.assertRaises(PermissionDenied):self.action(delivery,'cancel')
        self.assertEqual(self.client.post(reverse('workqueue:billing_mail'),{'version':1}).status_code,403)

    def test_owner_change_cancels_instead_of_redirecting_email(self):
        delivery=self.queue_mail()
        with self.settings(INTAKE_OWNER_EMAIL='changed@example.invalid'):
            self.assertTrue(cancel_ineligible_receipt_notice(delivery.notification))
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'canceled')
        self.assertEqual(delivery.notification.recipient,'owner@example.invalid')

    def test_revoked_expired_and_voided_delivery_cancels_receipt(self):
        delivery=self.queue_mail()
        self.delivery.revoked_at=timezone.now();self.delivery.save()
        self.assertTrue(cancel_ineligible_receipt_notice(delivery.notification))
        self.assertEqual(delivery.notification.body,'')
        self.assertEqual(balance(self.bill)['due'],0)

    def test_extra_recipient_cannot_be_smuggled_into_worker(self):
        delivery=self.queue_mail();delivery.notification.bcc_recipients=['intruder@example.invalid'];delivery.notification.save()
        self.assertTrue(cancel_ineligible_receipt_notice(delivery.notification))

    def test_worker_sends_pdf_only_to_owner_and_clears_bodies(self):
        delivery=self.queue_mail();deliver_pending()
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'sent')
        self.assertEqual(len(mail.outbox),1);message=mail.outbox[0]
        self.assertEqual(message.to,['owner@example.invalid']);self.assertEqual(message.cc,[]);self.assertEqual(message.bcc,[])
        self.assertEqual(len(message.attachments),1)
        attachment=message.attachments[0];self.assertEqual(attachment[0],self.receipt.number+'-test-only.pdf')
        text=' '.join(page.extract_text() for page in PdfReader(BytesIO(attachment[1])).pages)
        self.assertIn(self.receipt.number,text);self.assertIn('NO REAL MONEY RECEIVED',text)
        self.assertNotIn(self.attempt.payment_intent_id,text)
        self.assertEqual(delivery.notification.body,'');self.assertEqual(delivery.notification.html_body,'')
        deliver_pending();self.assertEqual(len(mail.outbox),1)

    def test_pdf_failure_is_known_unsent_and_does_not_touch_ledger(self):
        delivery=self.queue_mail()
        with patch('workqueue.payment_receipt_pdf.receipt_pdf',side_effect=RuntimeError('private provider detail')):
            deliver_pending()
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'failed')
        self.assertIn('no email was sent',delivery.notification.last_error)
        self.assertNotIn('private provider',delivery.notification.last_error)
        self.assertEqual(len(mail.outbox),0);self.assertEqual(balance(self.bill)['due'],0)

    def test_ambiguous_send_is_held_and_not_retried(self):
        delivery=self.queue_mail()
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=RuntimeError('private response')):
            deliver_pending()
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'unknown')
        self.assertNotIn('private response',delivery.notification.last_error)
        deliver_pending();self.assertEqual(len(mail.outbox),0)
        with self.assertRaises(ValidationError):self.action(delivery,'retry')

    def test_known_failed_send_can_be_retried_after_review(self):
        delivery=self.queue_mail();delivery.notification.state='failed';delivery.notification.save()
        self.action(delivery,'retry');delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'pending')
        with self.assertRaises(EditConflict):self.action(delivery,'retry',expected_state='failed')
        deliver_pending();self.assertEqual(len(mail.outbox),1)

    def test_unknown_delivery_can_only_be_confirmed_or_closed(self):
        delivery=self.queue_mail();delivery.notification.state='unknown';delivery.notification.save()
        self.action(delivery,'confirm_sent');delivery.notification.refresh_from_db()
        self.assertEqual(delivery.notification.state,'sent');self.assertEqual(delivery.notification.body,'')
        self.assertTrue(delivery.events.filter(action='Receipt delivery confirmed by owner').exists())
        deliver_pending();self.assertEqual(len(mail.outbox),0)

    def test_sending_or_sent_cannot_be_retried(self):
        delivery=self.queue_mail()
        for state in ['sending','sent']:
            delivery.notification.state=state;delivery.notification.save()
            with self.assertRaises(ValidationError):self.action(delivery,'retry')

    def test_stale_sending_becomes_unknown_without_resend(self):
        delivery=self.queue_mail();delivery.notification.state='sending'
        delivery.notification.last_attempt_at=timezone.now()-timedelta(minutes=20);delivery.notification.save()
        deliver_pending();delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'unknown')
        self.assertEqual(len(mail.outbox),0)

    def test_settings_off_does_not_recall_inflight_mail(self):
        config=self.enable();delivery=self.queue_mail();delivery.automatic=True;delivery.save()
        delivery.notification.state='sending';delivery.notification.save()
        save_settings(actor=self.actor,version=config.version,automatic_receipts=False)
        delivery.notification.refresh_from_db();self.assertEqual(delivery.notification.state,'sending')

    def test_payment_with_review_issue_is_not_emailed(self):
        self.attempt.issue_code='amount_mismatch';self.attempt.save()
        with self.assertRaises(ValidationError):self.queue_mail()
        self.assertEqual(balance(self.bill)['due'],0)

    def test_public_user_cannot_read_or_send_billing_mail(self):
        stranger=Browser();url=reverse('workqueue:billing_receipt_email',args=[self.receipt.pk])
        self.assertEqual(stranger.get(url).status_code,302);self.assertEqual(stranger.post(url,{}).status_code,302)
        self.assertEqual(self.public.get(reverse('workqueue:billing_mail')).status_code,302)

    def test_get_preview_does_not_queue_and_post_requires_review(self):
        url=reverse('workqueue:billing_receipt_email',args=[self.receipt.pk])
        response=self.client.get(url);self.assertContains(response,'Review before sending')
        self.assertFalse(NativeReceiptEmail.objects.exists())
        self.assertEqual(self.client.post(url,{'confirm':'on'}).status_code,400)
        token=response.context['send_form'].initial['review']
        self.assertEqual(self.client.post(url,{'review':token,'confirm':'on'}).status_code,302)
        self.assertEqual(NativeReceiptEmail.objects.count(),1)

    def test_review_tamper_or_owner_change_cannot_redirect_receipt(self):
        url=reverse('workqueue:billing_receipt_email',args=[self.receipt.pk]);response=self.client.get(url)
        token=response.context['send_form'].initial['review']
        self.assertEqual(self.client.post(url,{'review':token+'tampered','confirm':'on'}).status_code,400)
        with self.settings(INTAKE_OWNER_EMAIL='changed@example.invalid'):
            self.assertEqual(self.client.post(url,{'review':token,'confirm':'on'}).status_code,400)
        self.assertFalse(NativeReceiptEmail.objects.exists())

    def test_expired_review_has_clear_recovery_link(self):
        url=reverse('workqueue:billing_receipt_email',args=[self.receipt.pk]);response=self.client.get(url)
        token=response.context['send_form'].initial['review']
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+901):
            response=self.client.post(url,{'review':token,'confirm':'on'})
        self.assertContains(response,'Start a fresh review',status_code=400)
        self.assertFalse(NativeReceiptEmail.objects.exists())

    def test_csrf_is_required_for_owner_send(self):
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.actor)
        self.assertEqual(browser.post(reverse('workqueue:billing_receipt_email',args=[self.receipt.pk]),{'confirm':'on'}).status_code,403)

    def test_center_search_filter_and_private_headers(self):
        delivery=self.queue_mail();url=reverse('workqueue:billing_mail')
        response=self.client.get(url,{'q':self.receipt.number,'state':'pending'})
        self.assertContains(response,self.receipt.number);self.assertContains(response,'1 waiting')
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(len(self.client.get(url,{'q':'does not exist'}).context['receipt_page']),0)
        self.assertContains(self.client.get(url,{'state':'invalid'}),'Payment receipts')

    def test_receipt_email_can_be_sent_when_checkout_is_paused(self):
        with self.settings(NATIVE_STRIPE_TEST_CLIENT_CHECKOUT_ENABLED=False):self.queue_mail()
        self.assertEqual(NativeReceiptEmail.objects.count(),1)


@skipUnless(connection.vendor=='postgresql','Row-lock concurrency requires PostgreSQL.')
@override_settings(**SETTINGS)
class BillingMailConcurrencyTests(MailFixtures,TransactionTestCase):
    def setUp(self):self.seed_mail()

    def test_concurrent_queue_is_one_email_and_audit(self):
        def queue():
            close_old_connections()
            try:return queue_receipt(receipt_id=self.receipt.pk,actor=self.actor).pk
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:queue(),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(NativeReceiptEmail.objects.count(),1)
        self.assertEqual(NativeBillingMailEvent.objects.filter(action='Sandbox receipt email queued').count(),1)
