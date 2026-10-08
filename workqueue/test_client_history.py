from django.test import Client
from django.urls import reverse
from .models import ProjectAccess, Submission
from .test_receipts import SubmissionReceiptTests


class ClientHistoryTests(SubmissionReceiptTests):
    def test_verified_submitter_can_download_original_answers(self):
        item = self.submit(new_description='Original description', notes='Original client notes')
        receipt = self.receipt(item)
        submission = item.submissions.get()
        self.verify_receipt(self.client, receipt.body)
        item.description = 'Later staff description'
        item.internal_notes = 'PRIVATE STAFF NOTE'
        item.save()
        page = self.client.get(reverse('workqueue:tracking'))
        self.assertContains(page, 'Original description')
        self.assertContains(page, 'Original client notes')
        self.assertNotContains(page, 'PRIVATE STAFF NOTE')
        response = self.client.get(reverse('workqueue:receipt_download', args=[submission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Original description', response.content.decode())
        self.assertNotIn('Later staff description', response.content.decode())
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(Client().get(reverse('workqueue:receipt_download', args=[submission.pk])).status_code, 404)

    def test_project_access_does_not_expose_another_submitters_billing(self):
        item = self.submit(billing_street='Private billing street')
        submission = item.submissions.get()
        ProjectAccess.objects.create(project=item.project, email='collaborator@example.invalid')
        session = self.client.session
        from django.utils import timezone
        session['queue_verified_email'] = 'collaborator@example.invalid'
        session['queue_email_verified_until'] = timezone.now().timestamp()+3600
        session.save()
        page = self.client.get(reverse('workqueue:tracking'))
        self.assertContains(page, item.reference)
        self.assertNotContains(page, 'Private billing street')
        self.assertEqual(self.client.get(reverse('workqueue:receipt_download', args=[submission.pk])).status_code, 404)

    def test_revoked_project_access_blocks_receipt_and_history(self):
        item = self.submit()
        self.verify_receipt(self.client, self.receipt(item).body)
        from django.utils import timezone
        ProjectAccess.objects.filter(project=item.project).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(reverse('workqueue:receipt_download', args=[item.submissions.get().pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('workqueue:tracking')), item.reference)
