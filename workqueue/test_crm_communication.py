import io
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client as Browser, override_settings
from django.urls import reverse
from django.utils import timezone

from .crm_csv import apply_import, build_plan, export_csv, parse_csv
from .crm_mail import ComposeForm, compose_email, queue_morning_digest, save_preferences
from .crm import save_contact, merge_clients, log_activity
from .crm_overview import timeline
from .models import (Client, ClientContact, ClientEmail, ClientEvent, ClientFollowUp, ClientImport,
                     CRMSettings, MorningDigest, NotificationDelivery, Status)
from .notifications import deliver_pending
from .rich_email import clean_email_html
from .services import EditConflict, create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True)
class CRMCommunicationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('mail-owner', 'owner@example.invalid', 'local-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.contact, self.account = self.item.client_contact, self.item.client_contact.client
        self.client.force_login(self.owner)
        self.url = reverse('workqueue:compose_email', args=[self.account.pk])

    def compose_data(self, **values):
        data = {'token': str(uuid.uuid4()), 'contacts': [str(self.contact.pk)], 'project': self.item.project_id or '',
                'subject': 'Project drawings', 'body_html': '<p>Hello <strong>Alex</strong>.</p>',
                'cc': 'builder@example.invalid', 'bcc': 'private@example.invalid', 'include_signature': 'on'}
        data.update(values)
        return data

    def form(self, **values):
        return ComposeForm(self.compose_data(**values), client=self.account)

    def queue_email(self, **values):
        form = self.form(**values)
        self.assertTrue(form.is_valid(), form.errors)
        return compose_email(client_id=self.account.pk, item_id=self.item.pk, data=form.cleaned_data, actor=self.owner)

    def test_rich_email_allowlist_removes_active_markup_and_unsafe_links(self):
        html, text = clean_email_html('<p onclick="oops()">Hi<strong>there</strong><img src="https://spy.invalid">'
            '<script>secret()</script><a href="javascript:alert(1)">bad</a><a href="https://example.invalid">good</a></p>')
        self.assertNotIn('onclick', html)
        self.assertNotIn('javascript', html)
        self.assertNotIn('secret', text)
        self.assertNotIn('<img', html)
        self.assertIn('<strong>', html)
        self.assertIn('https://example.invalid', html)

    def test_recipient_validation_cross_client_archive_headers_and_blank_message(self):
        foreign = create_work(data=work_data(contact_email='foreign@example.invalid'), actor=self.owner)[0].client_contact
        for values in ({'contacts':[foreign.pk]}, {'cc':'a@example.invalid\nBcc:x@example.invalid'},
                       {'subject':'a\r\nBcc:b@example.invalid'}, {'body_html':'<p><br></p>'},
                       {'bcc':self.contact.email}, {'cc':','.join(f'a{i}@example.invalid' for i in range(26))}):
            self.assertFalse(self.form(**values).is_valid())

    def test_queue_email_once_signature_and_no_queue_change(self):
        CRMSettings.objects.create(signature_html='<p>Mike · <strong>Provost Home Design</strong></p>')
        data = self.compose_data()
        form = ComposeForm(data, client=self.account)
        self.assertTrue(form.is_valid())
        first, created = compose_email(client_id=self.account.pk, item_id=self.item.pk, data=form.cleaned_data, actor=self.owner)
        again, created_again = compose_email(client_id=self.account.pk, item_id=self.item.pk, data=form.cleaned_data, actor=self.owner)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(ClientEmail.objects.count(), 1)
        self.assertIn('Provost Home Design', first.body_html)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Status.NEW)
        self.assertEqual(len(mail.outbox), 0)

    def test_actual_mail_multipart_cc_bcc_and_retained_history(self):
        record, _ = self.queue_email()
        deliver_pending()
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, [self.contact.email])
        self.assertEqual(sent.cc, ['builder@example.invalid'])
        self.assertEqual(sent.bcc, ['private@example.invalid'])
        mime = sent.message().as_string()
        self.assertNotIn('Bcc:', mime)
        self.assertNotIn('private@example.invalid', mime)
        self.assertIn('multipart/alternative', mime)
        record.delivery.refresh_from_db()
        self.assertEqual(record.delivery.state, 'sent')
        self.assertEqual(record.delivery.html_body, '')
        record.refresh_from_db()
        self.assertIn('<strong>Alex</strong>', record.body_html)
        deliver_pending()
        self.assertEqual(len(mail.outbox), 1)

    def test_archive_after_editor_open_cannot_send(self):
        form = self.form()
        self.assertTrue(form.is_valid())
        ClientContact.objects.filter(pk=self.contact.pk).update(archived_at=timezone.now())
        with self.assertRaises(ValidationError):
            compose_email(client_id=self.account.pk, item_id=self.item.pk, data=form.cleaned_data, actor=self.owner)

    def test_signature_skip_and_timeline_link(self):
        CRMSettings.objects.create(signature_html='<p>SECRET SIGNATURE</p>')
        message, _ = self.queue_email(include_signature='')
        self.assertNotIn('SECRET SIGNATURE', message.body_text)
        page = timeline(self.account, 1)
        entry = next(row for row in page if row['title'] == 'Client email · Pending')
        self.assertEqual(entry['link'], reverse('workqueue:email_detail', args=[message.pk]))
        self.assertContains(self.client.get(entry['link']), 'private@example.invalid')

    def test_compose_popup_valid_invalid_and_retry(self):
        self.assertContains(self.client.get(self.url + f'?item={self.item.pk}'), 'To — select client contacts')
        invalid = self.client.post(self.url, self.compose_data(body_html=''))
        self.assertEqual(invalid.status_code, 400)
        data = self.compose_data()
        first = self.client.post(self.url, data)
        self.assertEqual(first.status_code, 302)
        second = self.client.post(self.url, data)
        self.assertEqual(second.url, first.url)
        self.assertEqual(ClientEmail.objects.count(), 1)
        self.assertEqual(self.client.get(self.url + '?item=invalid').status_code, 400)

    def test_settings_off_by_default_rich_signature_toggle_conflict(self):
        url = reverse('workqueue:communication_settings')
        response = self.client.get(url)
        self.assertFalse(response.context['form'].instance.morning_reminders)
        self.assertEqual(self.client.post(url, {'version':1, 'signature_html':'<p>Mike<script>bad()</script></p>',
            'morning_reminders':'on'}).status_code, 302)
        preferences = CRMSettings.objects.get(pk=1)
        self.assertTrue(preferences.morning_reminders)
        self.assertNotIn('script', preferences.signature_html)
        self.assertEqual(self.client.post(url, {'version':1, 'signature_html':'replace'}).status_code, 400)

    def monday(self, hour=8):
        return datetime(2026, 10, 12, hour, 1, tzinfo=ZoneInfo('America/New_York'))

    def test_digest_only_actionable_weekdays_once_and_private(self):
        CRMSettings.objects.create(morning_reminders=True)
        self.assertIsNone(queue_morning_digest(self.monday()))
        task = ClientFollowUp.objects.create(client=self.account, title='Call about bank plans',
            due_date=self.monday().date(), notes='PRIVATE NOTE NOT IN EMAIL')
        self.assertIsNone(queue_morning_digest(self.monday(7)))
        self.assertIsNone(queue_morning_digest(self.monday() - timedelta(days=1)))
        delivery = queue_morning_digest(self.monday())
        self.assertEqual(delivery.recipient, 'owner@example.invalid')
        self.assertIn('Call about bank plans', delivery.body)
        self.assertNotIn(task.notes, delivery.body)
        self.assertIsNone(queue_morning_digest(self.monday(9)))
        self.assertEqual(MorningDigest.objects.count(), 1)

    def test_digest_queue_due_and_overdue_closed_excluded_disable_cancels(self):
        prefs = CRMSettings.objects.create(morning_reminders=True)
        self.item.committed_due_date = self.monday().date() - timedelta(days=1)
        self.item.save()
        delivery = queue_morning_digest(self.monday())
        self.assertIn(self.item.reference, delivery.body)
        save_preferences({'version':1, 'morning_reminders':False, 'signature_html':''}, self.owner)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, 'canceled')
        self.item.status = Status.COMPLETED
        self.item.save()
        prefs.refresh_from_db()
        prefs.morning_reminders = True
        prefs.save()
        self.assertIsNone(queue_morning_digest(self.monday() + timedelta(days=1)))

    def csv_batch(self, text):
        rows = parse_csv(SimpleUploadedFile('clients.csv', text.encode('utf-8')))
        return ClientImport.objects.create(actor=self.owner, rows=rows, plan=build_plan(rows))

    def test_user_csv_format_exact_match_fills_blanks_preserves_notes_and_jobs(self):
        self.account.billing_city = ''
        self.account.internal_notes = 'Keep existing note'
        self.account.save()
        text = 'Organization,First Name,Last Name,Email,Phone,Address Line 1,Address Line 2,City,Province/State,Country,Postal Code,Notes\n'
        text += f',Other,Name,{self.contact.email},5559998888,Main Street,Unit 2,Boston,MA,United States,02101,New note\n'
        batch = self.csv_batch(text)
        self.assertEqual(batch.plan[0]['target'], str(self.account.pk))
        count = Client.objects.count()
        result = apply_import(batch.pk, {}, self.owner)
        self.assertEqual(Client.objects.count(), count)
        self.account.refresh_from_db(); self.contact.refresh_from_db(); self.item.refresh_from_db()
        self.assertEqual(self.account.billing_city, 'Boston')
        self.assertIn('Keep existing note', self.account.internal_notes)
        self.assertIn('New note', self.account.internal_notes)
        self.assertNotEqual(self.contact.full_name, 'Other Name')
        self.assertEqual(self.item.client_contact_id, self.contact.pk)
        self.assertEqual(apply_import(batch.pk, {}, self.owner), result)

    def test_new_org_multiple_contacts_and_duplicate_rows_create_one_client(self):
        batch = self.csv_batch('Organization,Full Name,Email,Phone\nAcme Building,Anne,anne@example.invalid,5085550101\n'
            'Acme Building,Bob,bob@example.invalid,5085550102\nAcme Building,Anne,anne@example.invalid,5085550101\n')
        result = apply_import(batch.pk, {}, self.owner)
        self.assertEqual(result['new_clients'], 1)
        target = Client.objects.get(name='Acme Building')
        self.assertEqual(target.contacts.count(), 2)
        again = self.csv_batch('Organization,Full Name,Email,Phone\nAcme Building,Anne,anne@example.invalid,5085550101\n')
        apply_import(again.pk, {}, self.owner)
        self.assertEqual(Client.objects.filter(name='Acme Building').count(), 1)

    def test_same_name_needs_choice_can_connect_skip_or_create(self):
        batch = self.csv_batch(f'Full Name,Email,Phone\n{self.contact.full_name},different@example.invalid,{self.contact.phone}\n')
        self.assertTrue(batch.plan[0]['candidates'])
        with self.assertRaises(ValidationError):
            apply_import(batch.pk, {}, self.owner)
        count = Client.objects.count()
        apply_import(batch.pk, {'0':str(self.account.pk)}, self.owner)
        self.assertEqual(Client.objects.count(), count)
        self.assertTrue(self.account.contacts.filter(email='different@example.invalid').exists())

    def test_invalid_row_skip_and_csv_validation(self):
        batch = self.csv_batch('Full Name,Email\nBad,bad@@example.invalid\nGood,good@example.invalid\n')
        result = apply_import(batch.pk, {}, self.owner)
        self.assertEqual(result['skipped'], 1)
        self.assertTrue(ClientContact.objects.filter(email='good@example.invalid').exists())
        for text in ('Unknown,Column\na,b\n', 'Name,Email,email\na,b,c\n', 'Name,Email\na,b,c\n'):
            with self.assertRaises(ValidationError):
                self.csv_batch(text)

    def test_import_preview_expiry_and_concurrent_changes_roll_back(self):
        batch = self.csv_batch(f'Full Name,Email\nNew,new@example.invalid\nKnown,{self.contact.email}\n')
        Client.objects.filter(pk=self.account.pk).update(version=self.account.version + 1)
        with self.assertRaises(EditConflict):
            apply_import(batch.pk, {}, self.owner)
        self.assertFalse(ClientContact.objects.filter(email='new@example.invalid').exists())
        batch.created_at = timezone.now() - timedelta(hours=3)
        batch.save()
        with self.assertRaises(ValidationError):
            apply_import(batch.pk, {}, self.owner)

    def test_missing_email_reimport_review_connect_does_not_duplicate(self):
        batch = self.csv_batch('Full Name,Phone\nNo Email,5085550105\n')
        apply_import(batch.pk, {}, self.owner)
        target = Client.objects.get(name='No Email')
        second = self.csv_batch('Full Name,Phone\nNo Email,5085550105\n')
        apply_import(second.pk, {'0':str(target.pk)}, self.owner)
        self.assertEqual(target.contacts.count(), 1)

    def test_search_private_client_job_activity_task_and_email(self):
        self.account.internal_notes = 'Blueberry profile'
        self.account.save()
        self.item.internal_notes = 'Blueberry job'
        self.item.save()
        self.account.refresh_from_db()
        log_activity(client_id=self.account.pk, version=self.account.version, kind='call', note='Raspberry activity', actor=self.owner)
        ClientFollowUp.objects.create(client=self.account, title='Cherry reminder', due_date=timezone.localdate())
        self.queue_email(body_html='<p>Strawberry email</p>')
        for query in ['Blueberry profile', 'Blueberry job', 'Raspberry activity', 'Cherry reminder', 'Strawberry email']:
            response = self.client.get(reverse('workqueue:clients'), {'q': query})
            self.assertContains(response, reverse('workqueue:client_detail', args=[self.account.pk]))

    def test_export_formula_defense_utf8_and_roundtrip(self):
        self.account.internal_notes = '=HYPERLINK("bad")'
        self.account.save()
        exported = export_csv()
        self.assertTrue(exported.startswith('\ufeff'))
        self.assertIn("'=HYPERLINK", exported)
        batch = self.csv_batch(exported)
        count = Client.objects.count()
        apply_import(batch.pk, {}, self.owner)
        self.assertEqual(Client.objects.count(), count)

    def test_import_http_preview_required_and_retry(self):
        url = reverse('workqueue:import_clients')
        result = self.client.post(url, {'file':SimpleUploadedFile('clients.csv', b'Full Name,Email\nTest,test@example.invalid\n')})
        self.assertEqual(result.status_code, 302)
        self.assertContains(self.client.get(result.url), 'Review client import')
        self.assertEqual(self.client.post(result.url).status_code, 400)
        self.assertEqual(self.client.post(result.url, {'confirmed':'on'}).status_code, 302)
        self.assertContains(self.client.get(result.url), 'Import complete')
        self.client.post(result.url, {'confirmed':'on'})
        self.assertEqual(ClientContact.objects.filter(email='test@example.invalid').count(), 1)

    def test_auth_permissions_csrf_and_other_import_owner(self):
        anonymous = Browser()
        for url in [self.url, reverse('workqueue:communication_settings'), reverse('workqueue:import_clients'), reverse('workqueue:export_clients')]:
            self.assertEqual(anonymous.get(url).status_code, 302)
        strict = Browser(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(strict.post(self.url, self.compose_data()).status_code, 403)
        reader = get_user_model().objects.create_user('read-only', is_staff=True)
        from django.contrib.auth.models import Permission
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        batch = self.csv_batch('Full Name,Email\nSample,sample@example.invalid\n')
        with self.assertRaises(Exception):
            apply_import(batch.pk, {}, reader)

    def test_merged_profile_notes_search_and_email_history_follow_current_client(self):
        other = create_work(data=work_data(contact_email='merged@example.invalid', company='Merged Company'), actor=self.owner)[0].client_contact.client
        other.internal_notes = 'Original archived note'
        other.save()
        self.account.refresh_from_db()
        merge_clients(source_id=other.pk, source_version=other.version, target_id=self.account.pk,
                      target_version=self.account.version, actor=self.owner)
        response = self.client.get(reverse('workqueue:clients'), {'q':'Original archived note'})
        self.assertContains(response, reverse('workqueue:client_detail', args=[self.account.pk]))

    def test_same_origin_popup_headers_do_not_weaken_other_staff_pages(self):
        response = self.client.get(self.url)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        message, _ = self.queue_email()
        self.assertEqual(self.client.get(reverse('workqueue:email_detail', args=[message.pk]))['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(self.client.get(reverse('workqueue:communication_settings'))['X-Frame-Options'], 'DENY')
