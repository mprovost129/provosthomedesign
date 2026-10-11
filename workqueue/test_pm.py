import copy
import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client as Browser, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, NotificationDelivery, ClientContact, CRMSettings
from .pm_models import Proposal, ProposalRevision, ProjectTask, ProjectPlan, ProposalTemplate
from .pm_services import (save_plan, save_task, change_task, save_proposal, send_proposal,
    revise_proposal, withdraw_proposal, respond, snapshot_for, checked_draft)
from .pm_pdf import proposal_pdf
from .services import create_work, EditConflict
from .tests import work_data


def proposal_data(**changes):
    data = {'title':'Addition plans', 'scope':'Design an addition.', 'deliverables':'PDF plan set',
        'revisions':'One review round', 'exclusions':'Structural engineering', 'timeline':'To be agreed',
        'terms':'Custom test terms', 'valid_until':(timezone.localdate()+timedelta(days=20)).isoformat(),
        'pricing':'fixed', 'lines':[{'description':'Design services', 'quantity':'1.25', 'rate':'125.50'}]}
    data.update(changes)
    return data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_PUBLIC_BASE_URL='https://testserver', INTAKE_OWNER_EMAIL='owner@example.invalid')
class ProjectWorkspaceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('pm-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.project = self.item.project
        self.contact = self.item.client_contact
        self.billing_client = self.contact.client
        self.client.force_login(self.owner)

    def draft(self, **changes):
        return save_proposal(self.project.pk, self.billing_client.pk, proposal_data(**changes), uuid.uuid4(), 0, self.owner)

    def sent(self, **changes):
        draft = self.draft(**changes)
        revision = send_proposal(draft.pk, draft.version, self.contact.pk, uuid.uuid4(), self.owner)
        draft.refresh_from_db()
        return draft, revision

    def test_staff_workspace_lists_submission_files_clients_and_linked_jobs(self):
        sibling = create_work(data=work_data(kind='update', project=self.project), actor=self.owner)[0]
        response = self.client.get(reverse('workqueue:project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item.reference)
        self.assertContains(response, sibling.reference)
        self.assertContains(response, self.billing_client.name)
        self.assertContains(response, 'Original submitted answers')

    def test_private_pages_require_staff_and_permissions(self):
        self.client.logout()
        for name,args in [('projects', []), ('project_detail', [self.project.pk]), ('proposal_templates', []), ('freshbooks_connection', [])]:
            self.assertEqual(self.client.get(reverse('workqueue:'+name, args=args)).status_code, 302)
        staff = get_user_model().objects.create_user('limited-pm', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('workqueue:projects')).status_code, 403)

    def test_project_plan_optimistic_version_and_no_queue_changes(self):
        before = list(WorkItem.objects.values_list('pk', 'version', 'status', 'queue_order'))
        data = dict(scope='Working scope', internal_notes='Private', target_date=None, estimated_hours=Decimal('20'))
        plan = save_plan(self.project.pk, 0, data, self.owner)
        with self.assertRaises(EditConflict):
            save_plan(self.project.pk, 0, data, self.owner)
        self.assertEqual(plan.version, 1)
        self.assertEqual(before, list(WorkItem.objects.values_list('pk', 'version', 'status', 'queue_order')))

    def test_tasks_idempotent_and_conflict_checked(self):
        data = dict(title='Review survey', due_date=timezone.localdate(), notes='Private task', token=uuid.uuid4())
        task = save_task(self.project.pk, data, self.owner)
        self.assertEqual(save_task(self.project.pk, data, self.owner).pk, task.pk)
        change_task(self.project.pk, task.pk, 1, 'complete', self.owner)
        with self.assertRaises(EditConflict):
            change_task(self.project.pk, task.pk, 1, 'reopen', self.owner)
        change_task(self.project.pk, task.pk, 2, 'reopen', self.owner)
        task.refresh_from_db(); self.assertIsNone(task.completed_at)
        change_task(self.project.pk, task.pk, 3, 'cancel', self.owner)
        task.refresh_from_db(); self.assertIsNotNone(task.canceled_at)

    def test_draft_money_recomputed_decimal_and_bad_lines_rejected(self):
        data = checked_draft(proposal_data(total='1'))
        self.assertEqual(data['total'], '156.88')
        for quantity,rate in [('0', '1'), ('NaN', '2'), ('1', '-2')]:
            with self.assertRaises(ValidationError):
                checked_draft(proposal_data(lines=[{'description':'Service', 'quantity':quantity, 'rate':rate}]))
        with self.assertRaises(ValidationError):
            checked_draft(proposal_data(lines=[]))

    def test_draft_create_idempotent_edit_and_sent_immutable(self):
        token = uuid.uuid4()
        proposal = save_proposal(self.project.pk, self.billing_client.pk, proposal_data(), token, 0, self.owner)
        self.assertEqual(save_proposal(self.project.pk, self.billing_client.pk, proposal_data(title='Duplicate'), token, 0, self.owner).pk, proposal.pk)
        self.assertEqual(Proposal.objects.count(), 1)
        with self.assertRaises(EditConflict):
            save_proposal(self.project.pk, self.billing_client.pk, proposal_data(), token, 0, self.owner, proposal.pk)
        revision = send_proposal(proposal.pk, 1, self.contact.pk, uuid.uuid4(), self.owner)
        with self.assertRaises(ValidationError):
            save_proposal(self.project.pk, self.billing_client.pk, proposal_data(), token, 2, self.owner, proposal.pk)
        self.project.name = 'Changed project'; self.project.save()
        self.billing_client.name = 'Changed client'; self.billing_client.save()
        revision.refresh_from_db()
        self.assertNotEqual(revision.snapshot['project_name'], 'Changed project')
        self.assertNotEqual(revision.snapshot['client_name'], 'Changed client')

    def test_send_idempotent_and_recipient_must_belong_to_client(self):
        proposal = self.draft()
        other = create_work(data=work_data(contact_email='other@example.invalid'), actor=self.owner)[0]
        with self.assertRaises(ValidationError):
            send_proposal(proposal.pk, 1, other.client_contact_id, uuid.uuid4(), self.owner)
        token = uuid.uuid4()
        first = send_proposal(proposal.pk, 1, self.contact.pk, token, self.owner)
        again = send_proposal(proposal.pk, 1, self.contact.pk, token, self.owner)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_private_access_hashed_fragment_and_scoped_pdf_no_internal_notes(self):
        self.project.plan = save_plan(self.project.pk, 0, dict(scope='', internal_notes='OWNER SECRET', target_date=None, estimated_hours=None), self.owner)
        proposal, revision = self.sent()
        body = revision.invitation.notification.body
        raw = parse_qs(body.split('#', 1)[1].split('\n', 1)[0])['token'][0]
        self.assertEqual(revision.invitation.digest, hashlib.sha256(raw.encode()).hexdigest())
        guest = Browser()
        url = reverse('workqueue:proposal_public', args=[revision.pk])
        self.assertEqual(guest.get(url).status_code, 403)
        response = guest.post(reverse('workqueue:proposal_access'), {'token':raw})
        self.assertRedirects(response, url)
        response = guest.get(url)
        self.assertContains(response, 'Addition plans')
        self.assertNotContains(response, 'OWNER SECRET')
        self.assertNotContains(response, 'googletagmanager')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(guest.get(reverse('workqueue:proposal_public_pdf', args=[revision.pk])).status_code, 200)
        other, other_revision = self.sent(title='Other private proposal')
        self.assertEqual(guest.get(reverse('workqueue:proposal_public', args=[other_revision.pk])).status_code, 403)

    def test_accept_records_once_and_preserves_queue(self):
        proposal, revision = self.sent()
        before = list(WorkItem.objects.values_list('pk', 'status', 'queue_order'))
        data = dict(action='accept', full_name='Test Client', agreement=True, note='Thank you')
        accepted = respond(revision.invitation.pk, data)
        again = respond(revision.invitation.pk, data)
        self.assertEqual(accepted.pk, again.pk)
        self.assertEqual(NotificationDelivery.objects.count(), 3)
        self.assertEqual(accepted.accepted_name, 'Test Client')
        self.assertEqual(accepted.state, 'accepted')
        self.assertEqual(before, list(WorkItem.objects.values_list('pk', 'status', 'queue_order')))
        self.assertFalse(hasattr(proposal, 'billing_invoice'))

    def test_accept_agreement_changes_and_expiry(self):
        proposal, revision = self.sent()
        with self.assertRaises(ValidationError):
            respond(revision.invitation.pk, dict(action='accept', full_name='Test', agreement=False))
        with self.assertRaises(ValidationError):
            respond(revision.invitation.pk, dict(action='changes', full_name='Test', note=''))
        revision.invitation.expires_at = timezone.now()-timedelta(seconds=1); revision.invitation.save()
        with self.assertRaises(ValidationError):
            respond(revision.invitation.pk, dict(action='accept', full_name='Test', agreement=True))
        expired = self.draft(valid_until=(timezone.localdate()-timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationError):
            send_proposal(expired.pk, 1, self.contact.pk, uuid.uuid4(), self.owner)

    def test_changes_revision_and_withdraw_cancel_pending_message(self):
        proposal, first = self.sent()
        respond(first.invitation.pk, dict(action='changes', full_name='Test', note='Add garage.'))
        proposal.refresh_from_db()
        revise_proposal(proposal.pk, proposal.version, self.owner)
        with self.assertRaises(ValidationError):
            respond(first.invitation.pk, dict(action='accept', full_name='Test', agreement=True))
        proposal.refresh_from_db()
        second = send_proposal(proposal.pk, proposal.version, self.contact.pk, uuid.uuid4(), self.owner)
        self.assertEqual(second.sequence, 2)
        proposal.refresh_from_db()
        withdraw_proposal(proposal.pk, proposal.version, self.owner)
        second.invitation.notification.refresh_from_db()
        self.assertEqual(second.invitation.notification.state, 'canceled')
        with self.assertRaises(ValidationError):
            respond(second.invitation.pk, dict(action='accept', full_name='Test', agreement=True))
        self.assertEqual(ProposalRevision.objects.count(), 2)

    def test_views_templates_and_bad_uuid_return_proper_responses(self):
        proposal = self.draft()
        for name,args in [('projects', []), ('proposal_new', [self.project.pk]), ('proposal_edit', [self.project.pk, proposal.pk]),
                          ('proposal_detail', [proposal.pk]), ('proposal_templates', []), ('freshbooks_connection', []),
                          ('freshbooks_client', [self.billing_client.pk])]:
            self.assertEqual(self.client.get(reverse('workqueue:'+name, args=args)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse('workqueue:proposal_pdf', args=[proposal.pk])+'?revision=invalid').status_code, 404)
        self.assertEqual(self.client.get(reverse('workqueue:proposal_new', args=[self.project.pk])+'?item=invalid').status_code, 404)
        self.assertEqual(self.client.get(reverse('workqueue:proposal_new', args=[self.project.pk])+'?template=invalid').status_code, 404)
        self.assertTrue(proposal_pdf(snapshot_for(proposal)).startswith(b'%PDF'))

    def test_template_copy_and_staff_post_validate_service_lines(self):
        template = ProposalTemplate.objects.create(name='Test', scope='Template scope', deliverables='Template deliverables')
        response = self.client.get(reverse('workqueue:proposal_new', args=[self.project.pk])+f'?template={template.pk}')
        self.assertContains(response, 'Template scope')
        data = proposal_data(); data.update(client=str(self.billing_client.pk), token=str(uuid.uuid4()), version='0',
            **{'lines-TOTAL_FORMS':'1', 'lines-INITIAL_FORMS':'0', 'lines-0-description':'Design', 'lines-0-quantity':'1', 'lines-0-rate':'400'})
        response = self.client.post(reverse('workqueue:proposal_new', args=[self.project.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Proposal.objects.get().draft['total'], '400.00')

    def test_proposal_mutations_require_csrf(self):
        strict = Browser(enforce_csrf_checks=True); strict.force_login(self.owner)
        self.assertEqual(strict.post(reverse('workqueue:project_detail', args=[self.project.pk]), {'action':'plan'}).status_code, 403)

    def test_https_private_link_and_response_keep_csrf_and_same_origin_referrer(self):
        proposal, revision = self.sent()
        raw = parse_qs(revision.invitation.notification.body.split('#', 1)[1].split('\n', 1)[0])['token'][0]
        access = reverse('workqueue:proposal_access')
        public = reverse('workqueue:proposal_public', args=[revision.pk])
        guest = Browser(enforce_csrf_checks=True)
        page = guest.get(access, secure=True)
        # Native form POSTs in iOS browsers can have Origin: null under
        # no-referrer. Permit only same-origin referrers; never trust null.
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', page['Cache-Control'])
        csrf = guest.cookies['csrftoken'].value
        response = guest.post(access, {'token':raw, 'csrfmiddlewaretoken':csrf},
                              secure=True, HTTP_REFERER='https://testserver'+access)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, public)
        page = guest.get(public, secure=True)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertContains(page, 'Addition plans')
        response = guest.post(public, {'action':'accept', 'full_name':'Test Client', 'agreement':True,
                                      'csrfmiddlewaretoken':csrf},
                              secure=True, HTTP_REFERER='https://testserver'+public)
        self.assertEqual(response.status_code, 302)
        revision.refresh_from_db()
        self.assertEqual(revision.state, 'accepted')

    def test_private_access_rejects_untrusted_origins_and_missing_csrf(self):
        proposal, revision = self.sent()
        raw = parse_qs(revision.invitation.notification.body.split('#', 1)[1].split('\n', 1)[0])['token'][0]
        access = reverse('workqueue:proposal_access')
        guest = Browser(enforce_csrf_checks=True)
        guest.get(access, secure=True)
        csrf = guest.cookies['csrftoken'].value
        for headers, token in [
            ({'HTTP_ORIGIN':'null'}, csrf),
            ({'HTTP_ORIGIN':'https://untrusted.example'}, csrf),
            ({'HTTP_REFERER':'https://untrusted.example/'}, csrf),
            ({}, csrf),
            ({'HTTP_ORIGIN':'https://testserver'}, ''),
        ]:
            with self.subTest(headers=headers, token_present=bool(token)):
                response = guest.post(access, {'token':raw, 'csrfmiddlewaretoken':token}, secure=True, **headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('proposal_invitation', guest.session)
        response = guest.post(access, {'token':raw, 'csrfmiddlewaretoken':csrf},
                              secure=True, HTTP_ORIGIN='https://testserver')
        self.assertEqual(response.status_code, 302)

    def test_project_task_in_optional_morning_digest_without_private_notes(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from .crm_mail import queue_morning_digest
        CRMSettings.objects.update_or_create(pk=1, defaults={'morning_reminders':True})
        now = datetime(2026, 10, 12, 8, 15, tzinfo=ZoneInfo('America/New_York'))
        save_task(self.project.pk, dict(token=uuid.uuid4(), title='Review test survey', due_date=now.date(), notes='PRIVATE TASK NOTE'), self.owner)
        delivery = queue_morning_digest(now=now)
        self.assertIn('Review test survey', delivery.body)
        self.assertNotIn('PRIVATE TASK NOTE', delivery.body)
        self.assertIsNone(queue_morning_digest(now=now))
