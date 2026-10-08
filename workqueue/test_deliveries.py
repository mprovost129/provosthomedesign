from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from .models import CompletedDelivery, CompletedFile, PendingUpload, NotificationDelivery, Status
from .test_receipts import SubmissionReceiptTests
from .test_client_intake import valid_pdf


class CompletedDeliveryTests(SubmissionReceiptTests):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('delivery-owner', 'owner@example.invalid', 'test-only')

    def prepare(self):
        item = self.submit()
        self.client.force_login(self.owner)
        url = reverse('workqueue:deliver_files', args=[item.pk])
        token = self.client.get(url).context['form'].initial['intake_token']
        result = self.client.post(reverse('workqueue:start_upload'), {'intake_token': token, 'file': valid_pdf('Final drawings.pdf')})
        data = {'intake_token': token, 'version': item.version, 'upload_ids': result.json()['id'], 'message': 'Here are your final drawings.'}
        preview = self.client.post(url, data)
        self.assertEqual(preview.status_code, 200, preview.content.decode()[:400])
        data.update(action='send', preview_signature=preview.context['signature'])
        return item, url, data

    def test_preview_send_and_repeat_do_not_duplicate_release(self):
        item, url, data = self.prepare()
        self.assertFalse(CompletedDelivery.objects.exists())
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content.decode()[:400])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(CompletedDelivery.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.status, Status.NEW)
        delivery = CompletedDelivery.objects.get()
        self.assertEqual(delivery.files.count(), 1)
        self.assertNotIn('PRIVATE', delivery.notification.body)
        self.assertIn('Final drawings.pdf', delivery.notification.body)
        self.assertEqual(item.submissions.count(), 1)
        file = delivery.files.get()
        self.assertEqual(file.upload.state, 'attached')
        browser = Client()
        self.assertEqual(browser.get(reverse('workqueue:completed_file', args=[file.pk])).status_code, 404)
        self.verify_receipt(browser, delivery.notification.body)
        result = browser.get(reverse('workqueue:completed_file', args=[file.pk]))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(b''.join(result.streaming_content), valid_pdf('Final drawings.pdf').read())
        self.assertContains(browser.get(reverse('workqueue:tracking')), 'Completed files delivered by Provost Home Design')
        self.assertContains(browser.get(reverse('workqueue:tracking')), 'Final drawings.pdf')

    def test_changed_recipient_or_message_requires_new_preview(self):
        item, url, data = self.prepare()
        changed = dict(data, message='Changed after preview')
        self.assertEqual(self.client.post(url, changed).status_code, 400)
        item.contact_email = 'different@example.invalid'; item.save()
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertFalse(CompletedDelivery.objects.exists())

    def test_client_cannot_use_staff_delivery_flow(self):
        item = self.submit()
        self.assertEqual(Client().get(reverse('workqueue:deliver_files', args=[item.pk])).status_code, 302)
        user = get_user_model().objects.create_user('limited', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('workqueue:deliver_files', args=[item.pk])).status_code, 403)
