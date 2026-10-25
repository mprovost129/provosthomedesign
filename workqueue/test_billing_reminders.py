import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
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
from .test_invoice_delivery import DeliveryFixtures
from .test_client_payments import SETTINGS
from .billing_reminder_models import NativeReminderSettings,NativeReminderPolicy,NativeInvoiceReminder
from .billing_reminders import (settings_record,save_settings,save_policy,policy_record,queue_reminder,
    queue_due_invoice_reminders,open_query,stage_for,cancel_ineligible_invoice_reminder,resolve_reminder)
from .billing_reminder_views import review_payload,SALT
from .models import NotificationDelivery
from .native_invoices import balance
from .test_payment_models import NativePaymentAttempt
from .services import EditConflict
from .notifications import deliver_pending


class ReminderFixtures(DeliveryFixtures):
    def seed_reminders(self):
        self.seed_delivery()
        self.bill.issue_date=timezone.localdate()-timedelta(days=35)
        self.bill.due_date=timezone.localdate()-timedelta(days=1)
        self.bill.snapshot.update(issue_date=self.bill.issue_date.isoformat(),due_date=self.bill.due_date.isoformat())
        self.bill.save()
        self.delivery=self.queue_copy()
        self.delivery.notification.state='sent';self.delivery.notification.body='';self.delivery.notification.html_body='';self.delivery.notification.save()
        self.client.force_login(self.actor)

    def review(self):
        self.bill.refresh_from_db()
        existing=NativeInvoiceReminder.objects.select_related('notification').filter(invoice=self.bill,stage=stage_for(self.bill)).first()
        return review_payload(self.bill,self.actor,existing,'owner@example.invalid')

    def queue(self,review=None):
        return queue_reminder(invoice_id=self.bill.pk,actor=self.actor,review=review or self.review())

    def config(self):
        config=save_settings(actor=self.actor,version=settings_record().version,enabled=True)
        config.enabled_since=timezone.now()-timedelta(days=20);config.save()
        return config

    def move_due(self,days):
        self.bill.due_date=timezone.localdate()-timedelta(days=days)
        self.bill.snapshot['due_date']=self.bill.due_date.isoformat();self.bill.save()

    def resolve(self,reminder,action):
        reminder.notification.refresh_from_db()
        return resolve_reminder(reminder_id=reminder.pk,actor=self.actor,action=action,state=reminder.notification.state,attempts=reminder.notification.attempts)


@override_settings(**SETTINGS)
class BillingReminderTests(ReminderFixtures,TestCase):
    def setUp(self):self.seed_reminders()

    def test_default_off_enable_does_not_backfill_overdue(self):
        self.assertFalse(settings_record().enabled)
        self.assertEqual(queue_due_invoice_reminders(),0)
        save_settings(actor=self.actor,version=1,enabled=True)
        self.assertEqual(queue_due_invoice_reminders(),0)
        self.assertFalse(NativeInvoiceReminder.objects.exists())

    def test_future_eligible_stage_queues_once(self):
        self.config();self.assertEqual(queue_due_invoice_reminders(),1);self.assertEqual(queue_due_invoice_reminders(),0)
        row=NativeInvoiceReminder.objects.get();self.assertEqual(row.stage,1);self.assertTrue(row.automatic)

    def test_missed_stages_queue_only_latest(self):
        self.config();self.move_due(16)
        self.assertEqual(queue_due_invoice_reminders(),1)
        self.assertEqual(list(NativeInvoiceReminder.objects.values_list('stage',flat=True)),[14])

    def test_at_least_48_hours_between_automatic_stages(self):
        self.config();first=self.queue();first.notification.state='sent';first.notification.save()
        self.move_due(8);self.assertEqual(queue_due_invoice_reminders(),0)
        NativeInvoiceReminder.objects.filter(pk=first.pk).update(created_at=timezone.now()-timedelta(hours=49),updated_at=timezone.now()-timedelta(hours=49))
        self.assertEqual(queue_due_invoice_reminders(),1)

    def test_delayed_actual_delivery_still_has_a_48_hour_cooldown(self):
        self.config();first=self.queue();first.notification.state='sent';first.notification.sent_at=timezone.now();first.notification.save()
        NativeInvoiceReminder.objects.filter(pk=first.pk).update(created_at=timezone.now()-timedelta(days=6),updated_at=timezone.now()-timedelta(days=6))
        self.move_due(8);self.assertEqual(queue_due_invoice_reminders(),0)

    def test_manual_is_owner_only_and_replay_is_idempotent(self):
        review=self.review();first=self.queue(review)
        self.assertEqual(self.queue(review).pk,first.pk);self.assertEqual(NativeInvoiceReminder.objects.count(),1)
        self.assertEqual(first.notification.recipient,'owner@example.invalid')
        self.assertNotIn('#token=',first.notification.body)
        self.assertNotIn('Internal accounting',first.notification.body)
        self.assertIn('staff sign-in required',first.notification.body)

    def test_same_stage_cannot_be_sent_twice(self):
        first=self.queue();first.notification.state='sent';first.notification.save()
        with self.assertRaises(ValidationError):self.queue()
        self.assertEqual(NativeInvoiceReminder.objects.count(),1)

    def test_full_payment_before_send_cancels_without_email(self):
        row=self.queue();self.entry(self.bill,amount='100')
        deliver_pending();row.notification.refresh_from_db()
        self.assertEqual(row.notification.state,'canceled');self.assertEqual(len(mail.outbox),0)
        self.assertEqual(balance(self.bill)['due'],0)

    def test_partial_payment_stale_review_and_queued_balance(self):
        old=self.review();self.entry(self.bill,amount='25')
        with self.assertRaises(EditConflict):self.queue(old)
        row=self.queue();self.assertEqual(row.snapshot['due'],'75.00')
        self.entry(self.bill,amount='25');deliver_pending();row.notification.refresh_from_db()
        self.assertEqual(row.notification.state,'canceled');self.assertEqual(len(mail.outbox),0)
        # Known-unsent cancellation may be explicitly reviewed again with the new balance.
        row=self.queue();self.assertEqual(row.snapshot['due'],'50.00');deliver_pending()
        self.assertEqual(len(mail.outbox),1)

    def test_credit_and_reversal_queries_match_authoritative_balance(self):
        payment=self.entry(self.bill,amount='25')
        self.entry(self.bill,kind='credit',amount='20',method='',reference='')
        self.assertEqual(open_query().get(pk=self.bill.pk).current_due,balance(self.bill)['due'])
        self.reverse(self.bill,payment)
        self.assertEqual(open_query().get(pk=self.bill.pk).current_due,Decimal('80.00'))

    def test_paid_credited_void_and_not_due_are_ineligible(self):
        self.entry(self.bill,kind='credit',amount='100',method='',reference='')
        with self.assertRaises(ValidationError):self.queue()
        self.assertFalse(open_query().exists())

    def test_not_overdue_cannot_be_queued(self):
        self.move_due(0)
        with self.assertRaises(ValidationError):self.queue()
        self.assertFalse(open_query().exists())

    def test_void_after_queue_cancels(self):
        row=self.queue();self.void(self.bill)
        self.assertTrue(cancel_ineligible_invoice_reminder(row.notification))
        self.assertEqual(row.notification.state,'canceled')

    def test_pause_and_snooze_cancel_manual_and_automatic_pending(self):
        row=self.queue()
        save_policy(invoice_id=self.bill.pk,actor=self.actor,version=1,paused=True,snooze_until=None)
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'canceled')
        with self.assertRaises(ValidationError):self.queue()
        save_policy(invoice_id=self.bill.pk,actor=self.actor,version=2,paused=False,snooze_until=timezone.localdate()+timedelta(days=7))
        with self.assertRaises(ValidationError):self.queue()
        save_policy(invoice_id=self.bill.pk,actor=self.actor,version=3,paused=False,snooze_until=None)
        self.assertEqual(self.queue().notification.state,'pending')

    def test_automatic_pause_preserves_manual_and_cancels_automatic(self):
        config=self.config();row=self.queue()
        save_settings(actor=self.actor,version=config.version,enabled=False)
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'pending')
        row.automatic=True;row.save();config=self.config()
        save_settings(actor=self.actor,version=config.version,enabled=False)
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'canceled')

    def test_optimistic_settings_and_snooze_validation(self):
        self.config()
        with self.assertRaises(EditConflict):save_settings(actor=self.actor,version=1,enabled=False)
        for day in [timezone.localdate(),timezone.localdate()+timedelta(days=367)]:
            with self.assertRaises(ValidationError):save_policy(invoice_id=self.bill.pk,actor=self.actor,version=1,paused=False,snooze_until=day)
        self.assertFalse(NativeReminderPolicy.objects.exists())

    def test_non_owner_can_read_but_not_send_or_change_controls(self):
        self.actor.is_superuser=False;self.actor.save()
        self.actor.user_permissions.add(*Permission.objects.filter(codename__in=['view_workitem','change_workitem']))
        self.assertEqual(self.client.get(reverse('workqueue:billing_followups')).status_code,200)
        with self.assertRaises(PermissionDenied):self.queue()
        with self.assertRaises(PermissionDenied):self.config()
        self.assertEqual(self.client.post(reverse('workqueue:billing_followups'),{'version':1,'enabled':'on'}).status_code,403)

    def test_payment_in_process_or_adjustment_issue_holds_reminder(self):
        for state,issue in [('processing',''),('paid','adjustment_unconfirmed')]:
            attempt=NativePaymentAttempt.objects.create(invoice=self.bill,invoice_version=self.bill.version,amount='25',method='bank',
                state=state,account_id='acct_fake',expires_at=timezone.now()+timedelta(days=1),actor=self.actor,issue_code=issue)
            with self.assertRaises(ValidationError):self.queue()
            attempt.delete()

    def test_refund_hold_prevents_reminder(self):
        with patch('workqueue.billing_reminders.refund_hold',return_value=True):
            with self.assertRaises(ValidationError):self.queue()

    def test_expired_original_link_is_not_renewed(self):
        digest=self.delivery.digest;expiry=timezone.now()-timedelta(days=1)
        self.delivery.expires_at=expiry;self.delivery.save()
        row=self.queue();deliver_pending();self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.digest,digest);self.assertEqual(self.delivery.expires_at,expiry)
        self.assertEqual(len(mail.outbox),1);self.assertNotIn('#token=',row.notification.body)

    def test_missing_unsent_or_revoked_original_copy_blocks_reminder(self):
        self.delivery.notification.state='unknown';self.delivery.notification.save()
        with self.assertRaises(ValidationError):self.queue()
        self.delivery.notification.state='sent';self.delivery.notification.save()
        self.delivery.revoked_at=timezone.now();self.delivery.save()
        with self.assertRaises(ValidationError):self.queue()

    def test_original_revoked_owner_changed_or_extra_bcc_cancels(self):
        row=self.queue();row.notification.bcc_recipients=['other@example.invalid'];row.notification.save()
        self.assertTrue(cancel_ineligible_invoice_reminder(row.notification))
        row=self.queue()
        with self.settings(INTAKE_OWNER_EMAIL='new@example.invalid'):
            self.assertTrue(cancel_ineligible_invoice_reminder(row.notification))
        self.assertEqual(row.owner_recipient,'owner@example.invalid')

    def test_worker_pdf_and_plain_html_escape_preserve_accounting(self):
        self.bill.snapshot['client_name']='A&B <Client>';self.bill.save();before=copy.deepcopy(balance(self.bill))
        row=self.queue();self.assertIn('A&B <Client>',row.notification.body);self.assertIn('A&amp;B &lt;Client&gt;',row.notification.html_body)
        deliver_pending();self.assertEqual(len(mail.outbox),1);message=mail.outbox[0]
        self.assertEqual(message.to,['owner@example.invalid']);self.assertEqual(message.cc,[]);self.assertEqual(message.bcc,[])
        self.assertEqual(len(message.attachments),1)
        attachment=message.attachments[0];self.assertEqual(attachment[0],self.bill.number+'-test-only.pdf')
        text=' '.join(page.extract_text() for page in PdfReader(BytesIO(attachment[1])).pages)
        self.assertIn(self.bill.number,text);self.assertNotIn('Internal accounting',text)
        self.assertIn('TEST ONLY',text);self.assertEqual(balance(self.bill),before)
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'sent');self.assertEqual(row.notification.body,'')
        deliver_pending();self.assertEqual(len(mail.outbox),1)

    def test_pdf_failure_is_known_unsent_and_fresh_review_can_retry(self):
        row=self.queue()
        with patch('workqueue.document_pdf.document_pdf',side_effect=RuntimeError('private details')):deliver_pending()
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'failed')
        self.assertEqual(len(mail.outbox),0);self.assertNotIn('private details',row.notification.last_error)
        self.queue();deliver_pending();self.assertEqual(len(mail.outbox),1)

    def test_unknown_cannot_retry_even_after_closing_and_holds_later_stages(self):
        row=self.queue()
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=RuntimeError('provider timeout')):deliver_pending()
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'unknown')
        with self.assertRaises(ValidationError):self.queue()
        self.resolve(row,'cancel')
        with self.assertRaises(ValidationError):self.queue()
        self.move_due(8)
        with self.assertRaises(ValidationError):self.queue()
        self.resolve(row,'confirm_sent');row.notification.refresh_from_db();self.assertEqual(row.notification.state,'sent')
        self.assertEqual(self.queue().stage,7)

    def test_settings_off_does_not_recall_inflight_and_unknown_sends_not_retried(self):
        row=self.queue();row.notification.state='sending';row.notification.last_attempt_at=timezone.now()-timedelta(minutes=20);row.notification.save()
        self.config();save_settings(actor=self.actor,version=settings_record().version,enabled=False)
        deliver_pending();row.notification.refresh_from_db();self.assertEqual(row.notification.state,'unknown')
        self.assertEqual(len(mail.outbox),0)

    def test_resolution_state_and_attempts_are_optimistic(self):
        row=self.queue()
        with self.assertRaises(EditConflict):resolve_reminder(reminder_id=row.pk,actor=self.actor,action='cancel',state='pending',attempts=99)

    def test_replaced_original_delivery_cancels_queued_reminder(self):
        row=self.queue();self.delivery.revoked_at=timezone.now();self.delivery.save()
        deliver_pending();row.notification.refresh_from_db()
        self.assertEqual(row.notification.state,'canceled');self.assertEqual(len(mail.outbox),0)

    def test_stage_that_went_stale_during_worker_downtime_is_canceled(self):
        row=self.queue()
        with patch('workqueue.billing_reminders.stage_for',return_value=7):
            self.assertTrue(cancel_ineligible_invoice_reminder(row.notification))

    def test_different_owner_and_missing_nonce_do_not_send(self):
        review=self.review();review['owner']='someone@example.invalid'
        with self.assertRaises(EditConflict):self.queue(review)
        review=self.review();del review['token']
        with self.assertRaises(ValidationError):self.queue(review)
        self.assertFalse(NativeInvoiceReminder.objects.exists())

    def test_reenable_keeps_old_overdue_invoices_manual(self):
        config=self.config();queue_due_invoice_reminders()
        save_settings(actor=self.actor,version=config.version,enabled=False)
        save_settings(actor=self.actor,version=settings_record().version,enabled=True)
        self.assertEqual(queue_due_invoice_reminders(),0)
        self.assertEqual(NativeInvoiceReminder.objects.count(),1)

    def test_anonymous_staff_csrf_and_no_store(self):
        url=reverse('workqueue:invoice_followup',args=[self.bill.pk]);stranger=Browser()
        self.assertEqual(stranger.get(url).status_code,302)
        strict=Browser(enforce_csrf_checks=True);strict.force_login(self.actor)
        self.assertEqual(strict.post(url,{}).status_code,403)
        self.assertIn('no-store',self.client.get(url)['Cache-Control'])

    def test_signed_review_get_no_send_tamper_expiry_and_stale_balance(self):
        url=reverse('workqueue:invoice_followup',args=[self.bill.pk]);response=self.client.get(url)
        self.assertContains(response,'Review before sending');self.assertFalse(NativeInvoiceReminder.objects.exists())
        token=response.context['send_form'].initial['review']
        self.assertEqual(self.client.post(url,{'action':'send','review':token+'x','confirm':'on'}).status_code,400)
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+901):
            self.assertEqual(self.client.post(url,{'action':'send','review':token,'confirm':'on'}).status_code,400)
        self.entry(self.bill,amount='25')
        self.assertEqual(self.client.post(url,{'action':'send','review':token,'confirm':'on'}).status_code,409)
        self.assertFalse(NativeInvoiceReminder.objects.exists())

    def test_form_send_policy_resolve_and_search_work(self):
        url=reverse('workqueue:invoice_followup',args=[self.bill.pk]);response=self.client.get(url)
        self.assertEqual(self.client.post(url,{'action':'send','review':response.context['send_form'].initial['review'],'confirm':'on'}).status_code,302)
        row=NativeInvoiceReminder.objects.get()
        self.assertEqual(self.client.post(url,{'action':'resolve','resolution_action':'cancel','reminder':row.pk,'state':'pending','attempts':0,'confirm':'on'}).status_code,302)
        row.notification.refresh_from_db();self.assertEqual(row.notification.state,'canceled')
        response=self.client.get(reverse('workqueue:billing_followups'),{'q':self.bill.number})
        self.assertEqual(len(response.context['page']),1);self.assertEqual(len(response.context['history']),1)
        response=self.client.get(reverse('workqueue:billing_followups'),{'q':'does-not-exist'})
        self.assertEqual(len(response.context['page']),0)


@skipUnless(connection.vendor=='postgresql','Row locking requires PostgreSQL.')
@override_settings(**SETTINGS)
class ReminderConcurrencyTests(ReminderFixtures,TransactionTestCase):
    def setUp(self):self.seed_reminders()

    def test_two_reviews_create_one_outbox_reminder(self):
        review=self.review()
        def worker():
            close_old_connections()
            try:return str(queue_reminder(invoice_id=self.bill.pk,actor=self.actor,review=review).pk)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(NativeInvoiceReminder.objects.count(),1)

    def test_two_schedulers_share_one_stage(self):
        self.config()
        def worker():
            close_old_connections()
            try:return queue_due_invoice_reminders()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sum(counts),1);self.assertEqual(NativeInvoiceReminder.objects.count(),1)
