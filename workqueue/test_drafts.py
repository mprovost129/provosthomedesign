import json
from datetime import timedelta
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .access import session_digest
from .models import IntakeDraft, PendingUpload, WorkItem, NotificationDelivery
from .test_receipts import SubmissionReceiptTests


class DraftRecoveryTests(SubmissionReceiptTests):
    def save(self, revision=0, **changes):
        return self.client.post(reverse('workqueue:draft'), json.dumps({
            'intake_token': self.token, 'revision': revision, 'kind': 'new',
            'contact_full_name': 'Alex Example', 'new_description': 'A saved answer', **changes}),
            content_type='application/json')

    def test_partial_draft_recovers_without_accepting_or_sending(self):
        response = self.save(terms_accepted=True, internal_notes='PRIVATE', recaptcha_token='secret')
        self.assertEqual(response.status_code, 200)
        draft = IntakeDraft.objects.get()
        self.assertNotIn('terms_accepted', draft.answers)
        self.assertNotIn('internal_notes', draft.answers)
        self.assertNotIn('recaptcha_token', draft.answers)
        self.assertFalse(WorkItem.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())
        restored = self.client.get(reverse('workqueue:submit'), {'resume': draft.pk})
        self.assertEqual(restored.context['form'].initial['contact_full_name'], 'Alex Example')
        self.assertFalse(restored.context['form'].initial['terms_accepted'])
        self.assertEqual(restored['Cache-Control'].find('no-store') >= 0, True)
        self.assertNotContains(Client().get(reverse('workqueue:submit'), {'resume': draft.pk}), 'A saved answer')

    def test_versions_do_not_overwrite_another_tab(self):
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save(new_description='stale overwrite').status_code, 409)
        self.assertEqual(IntakeDraft.objects.get().answers['new_description'], 'A saved answer')
        self.assertEqual(self.save(revision=1, new_description='fresh').status_code, 200)

    def test_expired_uploads_warn_and_terms_require_new_acceptance(self):
        upload_id = self.upload('Plans.pdf')
        self.save(upload_ids=upload_id)
        draft = IntakeDraft.objects.get()
        PendingUpload.objects.filter(pk=upload_id).update(expires_at=timezone.now()-timedelta(minutes=1))
        restored = self.client.get(reverse('workqueue:submit'), {'resume': draft.pk})
        self.assertContains(restored, 'Plans.pdf')
        self.assertEqual(restored.context['expired_files'], ['Plans.pdf'])
        self.assertEqual(restored.context['form'].initial['upload_ids'], '')
        self.save(revision=1)
        self.assertEqual(self.client.get(reverse('workqueue:submit'), {'resume': draft.pk}).context['expired_files'], ['Plans.pdf'])
        self.save(revision=2, expired_uploads_reviewed=True)
        self.assertEqual(IntakeDraft.objects.get().files, [])

    def test_submit_clears_draft_and_old_tab_cannot_recreate_it(self):
        self.save()
        self.submit()
        self.assertFalse(IntakeDraft.objects.exists())
        self.assertEqual(self.save(revision=1).status_code, 409)
        self.assertFalse(IntakeDraft.objects.exists())

    def test_invalid_submission_keeps_saved_answers_and_ready_file(self):
        upload_id = self.upload('Plans.pdf')
        self.save(upload_ids=upload_id)
        from .test_client_intake import new_answers
        response = self.client.post(reverse('workqueue:submit'), new_answers(intake_token=self.token,
            upload_ids=upload_id, categories=['Plans or survey'], terms_accepted=''))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(IntakeDraft.objects.exists())
        self.assertEqual(len(response.context['ready_uploads']), 1)

    def test_expired_drafts_and_foreign_files_are_not_restored(self):
        self.save(upload_ids='00000000-0000-0000-0000-000000000000')
        draft = IntakeDraft.objects.get()
        self.assertEqual(draft.files, [])
        IntakeDraft.objects.filter(pk=draft.pk).update(expires_at=timezone.now()-timedelta(days=1))
        response = self.client.get(reverse('workqueue:submit'), {'resume': draft.pk})
        self.assertIsNone(response.context['draft'])

    def test_draft_endpoint_is_csrf_protected_and_bounded(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('workqueue:draft'), '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.save(new_description='x'*20001).status_code, 409)
        self.assertFalse(IntakeDraft.objects.exists())

    def test_discard_does_not_change_submitted_work(self):
        self.save()
        result = self.client.post(reverse('workqueue:draft'), json.dumps({'action':'discard', 'intake_token':self.token}), content_type='application/json')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(IntakeDraft.objects.exists())
