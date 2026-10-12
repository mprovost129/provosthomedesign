"""Portal isolation, publication, and client/project relationship regressions."""
import uuid
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.test import Client as Browser, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import mint_email_token, new_intake_token, read_intake_token, session_digest
from .invoice_history_models import HistoricalInvoice, InvoiceCSVImport
from .freshbooks_models import FreshBooksInvoice
from .models import ClientContact, NotificationDelivery, PendingUpload, ProjectAccess, WorkProject
from .pm_models import ProjectPlan, Proposal
from .pm_services import save_proposal, send_proposal
from .portal_models import ClientProject, PortalDocument, PortalMember, ProjectPortal
from .portal_services import (document_visibility, google_document_url, grant_member, link_project_client,
                              publish_document, revoke_member)
from .services import EditConflict, create_work
from .test_pm import proposal_data
from .tests import work_data
from .uploads import private_storage


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, INTAKE_PUBLIC_BASE_URL='https://testserver')
class PortalTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('portal-owner', password=None)
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.project, self.contact = self.item.project, self.item.client_contact
        self.other = create_work(data=work_data(contact_email='other@example.invalid', company='Other company', project_name='Private other project'), actor=self.owner)[0]
        self.storage_dir = TemporaryDirectory()
        self.addCleanup(self.storage_dir.cleanup)
        override = override_settings(STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
            'intake_private': {'BACKEND': 'django.core.files.storage.FileSystemStorage', 'OPTIONS': {'location': self.storage_dir.name}}})
        override.enable()
        self.addCleanup(override.disable)

    def version(self, project=None):
        portal = ProjectPortal.objects.filter(project=project or self.project).first()
        return portal.version if portal else 1

    def grant(self, financial=False, project=None, contact=None):
        return grant_member(project or self.project, version=self.version(project), contact_id=(contact or self.contact).pk,
            financial_access=financial, actor=self.owner)

    def sign_in(self, email=None, browser=None):
        browser = browser or self.client
        session = browser.session
        session['queue_verified_email'] = email or self.contact.email
        session['queue_email_verified_until'] = (timezone.now()+timedelta(days=1)).timestamp()
        session.save()
        return browser

    def publish(self, document=None, *, project=None, kind='plan', client=None, url='', binding='binding', nonce=None):
        project = project or self.project
        nonce = nonce or uuid.uuid4()
        upload_ids = ''
        if not url:
            key = f'ready/{uuid.uuid4().hex}.pdf'
            content = b'%PDF-1.4\nTest plan\n%%EOF'
            private_storage().save(key, ContentFile(content))
            upload = PendingUpload.objects.create(intake_nonce=nonce, session_digest=binding,
                original_name='Drawing.pdf', storage_key=key, content_type='application/pdf',
                size_bytes=len(content), state='ready', expires_at=timezone.now()+timedelta(hours=1))
            upload_ids = str(upload.pk)
        return publish_document(project, version=self.version(project), title='Published plan', kind=kind,
            billing_client_id=client.pk if client else None, document_id=document.pk if document else None,
            note='Issued for review', external_url=url, upload_ids=upload_ids, nonce=nonce, binding=binding, actor=self.owner)

    def project_url(self, project=None):
        return reverse('workqueue:portal_project', args=[(project or self.project).pk])

    def document_url(self, revision, project=None):
        return reverse('workqueue:portal_document', args=[(project or self.project).pk, revision.pk])

    def sent_proposal(self, item=None, title='Shared proposal'):
        item = item or self.item
        proposal = save_proposal(item.project_id, item.client_contact.client_id, proposal_data(title=title), uuid.uuid4(), 0, self.owner)
        revision = send_proposal(proposal.pk, proposal.version, item.client_contact_id, uuid.uuid4(), self.owner)
        return proposal, revision

    def invoice(self, item=None, **changes):
        item = item or self.item
        data = dict(project=item.project, client=item.client_contact.client, account_id='TEST', customer_id=1,
            marker=uuid.uuid4().hex, payload={'private_note': 'SECRET PROVIDER PAYLOAD'}, state='linked',
            invoice_id=1, invoice_number='INV-PUBLIC', billing_status='sent', currency='USD', amount='150',
            paid='50', outstanding='100', last_sync_at=timezone.now())
        data.update(changes)
        return FreshBooksInvoice.objects.create(**data)

    def test_email_identity_alone_does_not_grant_project_access(self):
        self.sign_in()
        ProjectAccess.objects.create(project=self.project, email=self.contact.email)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('workqueue:portal_home')), self.project.name)

    def test_client_sees_only_approved_projects_and_can_switch(self):
        self.grant()
        second = create_work(data=work_data(project_name='Second approved project'), actor=self.owner)[0]
        self.grant(project=second.project, contact=second.client_contact)
        self.sign_in()
        response = self.client.get(reverse('workqueue:portal_home'))
        self.assertContains(response, self.project.name)
        self.assertContains(response, second.project.name)
        self.assertNotContains(response, self.other.project.name)
        self.assertEqual(self.client.get(self.project_url(self.other.project)).status_code, 404)

    def test_public_direct_project_link_reveals_no_project_before_signin(self):
        response = self.client.get(self.project_url())
        self.assertRedirects(response, reverse('workqueue:portal_home'))
        self.assertEqual(self.client.session['portal_return_project'], str(self.project.pk))

    def test_magic_link_returns_to_authorized_project_and_is_single_use(self):
        self.grant()
        self.client.get(self.project_url())
        _, raw = mint_email_token(self.contact.email, destination='portal')
        response = self.client.post(reverse('workqueue:confirm_access'), {'token': raw})
        self.assertRedirects(response, self.project_url())
        self.assertEqual(self.client.post(reverse('workqueue:confirm_access'), {'token': raw}).status_code, 400)

    def test_magic_link_never_redirects_to_unapproved_project(self):
        self.grant()
        self.client.get(self.project_url(self.other.project))
        _, raw = mint_email_token(self.contact.email, destination='portal')
        response = self.client.post(reverse('workqueue:confirm_access'), {'token': raw})
        self.assertRedirects(response, reverse('workqueue:portal_home'))

    @patch('workqueue.client_security.require_recaptcha')
    def test_signin_response_does_not_enumerate_and_unknown_email_is_not_sent_mail(self, captcha):
        self.grant()
        url = reverse('workqueue:portal_home')
        first = self.client.post(url, {'email': self.contact.email})
        second = self.client.post(url, {'email': 'unknown@example.invalid'})
        self.assertEqual(first.status_code, second.status_code)
        self.assertContains(first, 'If this address has portal access')
        self.assertContains(second, 'If this address has portal access')
        self.assertEqual(NotificationDelivery.objects.filter(recipient_kind='signin').count(), 1)

    @patch('workqueue.client_security.require_recaptcha')
    @patch('workqueue.portal_views.limited', return_value=False)
    def test_rate_limited_signin_does_not_queue_email(self, limited, captcha):
        self.grant()
        response = self.client.post(reverse('workqueue:portal_home'), {'email': self.contact.email})
        self.assertContains(response, 'If this address has portal access')
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_signin_requires_csrf(self):
        browser = Browser(enforce_csrf_checks=True)
        self.assertEqual(browser.post(reverse('workqueue:portal_home'), {'email': self.contact.email}).status_code, 403)

    def test_revocation_blocks_existing_session_and_all_file_versions(self):
        member = self.grant()
        first = self.publish()
        second = self.publish(first.document)
        self.sign_in()
        revoke_member(self.project, version=self.version(), member_id=member.pk, actor=self.owner)
        for url in [self.project_url(), self.document_url(first), self.document_url(second)]:
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_expired_session_cannot_download(self):
        self.grant()
        revision = self.publish()
        self.sign_in()
        session = self.client.session
        session['queue_email_verified_until'] = timezone.now().timestamp()-1
        session.save()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)

    def test_signout_clears_client_identity_and_proposal_session(self):
        self.sign_in()
        session = self.client.session
        session['proposal_invitation'] = 99
        session.save()
        self.assertRedirects(self.client.post(reverse('workqueue:portal_logout')), reverse('workqueue:portal_home'))
        self.assertNotIn('queue_verified_email', self.client.session)
        self.assertNotIn('proposal_invitation', self.client.session)

    def test_documents_have_immutable_versions_and_safe_download_headers(self):
        self.grant()
        first = self.publish()
        second = self.publish(first.document)
        self.sign_in()
        response = self.client.get(self.project_url())
        self.assertContains(response, 'Version history (2)')
        self.assertContains(response, 'Version 2')
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertNotEqual(first.storage_key, second.storage_key)
        download = self.client.get(self.document_url(first))
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertIn('no-store', download['Cache-Control'])
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(b''.join(download.streaming_content).startswith(b'%PDF-'))
        view = self.client.get(self.document_url(first)+'?view=1')
        self.assertEqual(view['Content-Type'], 'application/pdf')
        self.assertIn('inline', view['Content-Disposition'])
        b''.join(view.streaming_content)

    def test_forged_version_cannot_cross_project(self):
        self.grant()
        revision = self.publish(project=self.other.project)
        self.sign_in()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)

    def test_withdrawal_hides_all_versions_without_deleting_files(self):
        self.grant()
        revision = self.publish()
        document_visibility(self.project, version=self.version(), document_id=revision.document_id, visible=False, actor=self.owner)
        self.sign_in()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)
        self.assertTrue(private_storage().exists(revision.storage_key))
        document_visibility(self.project, version=self.version(), document_id=revision.document_id, visible=True, actor=self.owner)
        response = self.client.get(self.document_url(revision))
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)

    def test_missing_file_returns_404_without_storage_path(self):
        self.grant()
        revision = self.publish()
        private_storage().delete(revision.storage_key)
        self.sign_in()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)

    def test_external_document_requires_membership_and_explains_google_permissions(self):
        revision = self.publish(url='https://drive.google.com/file/d/abc123/view?usp=sharing')
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)
        self.grant()
        self.sign_in()
        response = self.client.get(self.document_url(revision))
        self.assertContains(response, 'Google account approved')
        self.assertContains(response, 'noopener noreferrer')

    def test_untrusted_url_formats_are_rejected(self):
        for url in ['http://drive.google.com/file/d/abc/view', 'https://drive.google.com.evil.invalid/file/d/abc/view',
                    'https://user@drive.google.com/file/d/abc/view', 'javascript:alert(1)',
                    'https://127.0.0.1/file/d/abc/view', 'https://drive.google.com/redirect',
                    'https://docs.google.com/document/d/a/edit#secret', 'https://drive.google.com:443/file/d/a/view']:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                google_document_url(url)

    def test_invoice_document_requires_financial_access_for_correct_client(self):
        revision = self.publish(kind='invoice', client=self.contact.client)
        self.grant()
        self.sign_in()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)
        self.grant(financial=True)
        response = self.client.get(self.document_url(revision))
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)

    def test_financial_access_does_not_show_other_companys_documents_on_same_project(self):
        link_project_client(self.project, version=self.version(), client_id=self.other.client_contact.client_id, actor=self.owner)
        revision = self.publish(kind='invoice', client=self.other.client_contact.client)
        self.grant(financial=True)
        self.sign_in()
        self.assertEqual(self.client.get(self.document_url(revision)).status_code, 404)

    def test_nonfinancial_members_cannot_read_proposals_or_invoices(self):
        _, revision = self.sent_proposal()
        self.invoice()
        self.grant()
        self.sign_in()
        response = self.client.get(self.project_url())
        self.assertNotContains(response, 'Shared proposal')
        self.assertNotContains(response, 'INV-PUBLIC')
        url = reverse('workqueue:portal_proposal', args=[self.project.pk, revision.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url+'?download=1').status_code, 404)

    def test_financial_members_see_sent_snapshots_but_never_drafts_or_internal_notes(self):
        _, revision = self.sent_proposal()
        self.invoice()
        save_proposal(self.project.pk, self.contact.client_id, proposal_data(title='SECRET DRAFT TITLE'), uuid.uuid4(), 0, self.owner)
        ProjectPlan.objects.create(project=self.project, internal_notes='SECRET PROJECT NOTES')
        self.grant(financial=True)
        self.sign_in()
        response = self.client.get(self.project_url())
        self.assertContains(response, 'Shared proposal')
        self.assertContains(response, 'INV-PUBLIC')
        self.assertNotContains(response, 'SECRET')
        url = reverse('workqueue:portal_proposal', args=[self.project.pk, revision.pk])
        self.assertContains(self.client.get(url), 'Design an addition.')
        self.assertTrue(self.client.get(url+'?download=1').content.startswith(b'%PDF-'))

    def test_provider_drafts_unreconciled_and_cross_client_invoices_hidden(self):
        self.invoice(invoice_id=1, invoice_number='SECRET-DRAFT', billing_status='draft')
        self.invoice(invoice_id=2, invoice_number='SECRET-UNKNOWN', state='unknown')
        self.invoice(invoice_id=3, invoice_number='SECRET-OTHER', client=self.other.client_contact.client)
        self.grant(financial=True)
        self.sign_in()
        self.assertNotContains(self.client.get(self.project_url()), 'SECRET')

    def test_cross_project_proposal_id_does_not_work(self):
        _, revision = self.sent_proposal(self.other)
        self.grant(financial=True)
        self.sign_in()
        self.assertEqual(self.client.get(reverse('workqueue:portal_proposal', args=[self.project.pk, revision.pk])).status_code, 404)

    def test_link_client_does_not_grant_access_or_send_email(self):
        link_project_client(self.project, version=self.version(), client_id=self.other.client_contact.client_id, actor=self.owner)
        self.assertTrue(ClientProject.objects.filter(project=self.project, client=self.other.client_contact.client).exists())
        self.assertFalse(PortalMember.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())
        self.client.force_login(self.owner)
        response = self.client.get(reverse('workqueue:client_detail', args=[self.other.client_contact.client_id]))
        self.assertContains(response, self.project.name)

    def test_explicit_client_connection_can_be_used_for_project_proposal(self):
        from .pm_forms import ProposalForm
        client = self.other.client_contact.client
        link_project_client(self.project, version=self.version(), client_id=client.pk, actor=self.owner)
        self.assertIn(client, ProposalForm(project=self.project).fields['client'].queryset)
        proposal = save_proposal(self.project.pk, client.pk, proposal_data(), uuid.uuid4(), 0, self.owner)
        self.assertEqual(proposal.client, client)

    def test_staff_form_labels_have_unique_control_ids(self):
        from html.parser import HTMLParser
        ids = []
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                ids.extend(value for key, value in attrs if key == 'id')
        self.client.force_login(self.owner)
        response = self.client.get(reverse('workqueue:portal_manage', args=[self.project.pk]))
        Parser().feed(response.content.decode())
        self.assertEqual(len(ids), len(set(ids)))

    def test_cannot_grant_contact_from_unrelated_project_until_linked(self):
        with self.assertRaises(ValidationError):
            self.grant(contact=self.other.client_contact)
        link_project_client(self.project, version=self.version(), client_id=self.other.client_contact.client_id, actor=self.owner)
        member = self.grant(contact=self.other.client_contact)
        self.assertEqual(member.email, self.other.contact_email)

    def test_contact_email_edit_does_not_move_access(self):
        self.grant()
        self.contact.email = 'changed@example.invalid'
        self.contact.save(update_fields=['email'])
        self.sign_in(self.contact.email)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)

    def test_stale_sharing_form_cannot_replace_grant(self):
        member = self.grant()
        with self.assertRaises(EditConflict):
            grant_member(self.project, version=1, contact_id=self.contact.pk, financial_access=True, actor=self.owner)
        member.refresh_from_db()
        self.assertFalse(member.financial_access)

    def test_staff_only_permissions_and_no_writes_on_manage_get(self):
        url = reverse('workqueue:portal_manage', args=[self.project.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), 'Direct project link')
        self.assertFalse(ProjectPortal.objects.exists())
        self.assertFalse(PortalMember.objects.exists())
        with self.assertRaises(PermissionDenied):
            grant_member(self.project, version=1, contact_id=self.contact.pk, financial_access=True,
                         actor=get_user_model().objects.create_user('ordinary-client'))

    def test_manage_rejects_missing_confirmation_and_invalid_ids(self):
        self.client.force_login(self.owner)
        url = reverse('workqueue:portal_manage', args=[self.project.pk])
        response = self.client.post(url, {'action': 'grant', 'version': 1, 'contact': self.contact.pk})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PortalMember.objects.exists())
        self.assertEqual(self.client.post(url, {'action': 'revoke', 'version': 1, 'member': 'not-an-id'}).status_code, 400)

    def test_publication_failure_rolls_back_document_and_preserves_upload(self):
        nonce = uuid.uuid4()
        upload = PendingUpload.objects.create(intake_nonce=nonce, session_digest='correct', original_name='plan.pdf',
            storage_key='ready/test.pdf', size_bytes=10, content_type='application/pdf', state='ready',
            expires_at=timezone.now()+timedelta(hours=1))
        with self.assertRaises(ValidationError):
            publish_document(self.project, version=1, title='Wrong session', kind='plan', billing_client_id=None,
                document_id=None, note='', external_url='', upload_ids=str(upload.pk), nonce=nonce, binding='wrong', actor=self.owner)
        self.assertFalse(PortalDocument.objects.exists())
        upload.refresh_from_db()
        self.assertEqual(upload.state, 'ready')

    def test_published_upload_cannot_be_reused(self):
        revision = self.publish()
        upload = revision.upload
        with self.assertRaises(ValidationError):
            publish_document(self.project, version=self.version(), title='Duplicate', kind='plan', billing_client_id=None,
                document_id=None, note='', external_url='', upload_ids=str(upload.pk), nonce=upload.intake_nonce,
                binding=upload.session_digest, actor=self.owner)

    def test_staff_publish_form_uses_bound_upload_and_stale_replay_cannot_duplicate(self):
        self.client.force_login(self.owner)
        url = reverse('workqueue:portal_manage', args=[self.project.pk])
        page = self.client.get(url)
        token = page.context['publication'].initial['intake_token']
        request = page.wsgi_request
        nonce, binding = read_intake_token(request, token), session_digest(request)
        upload = PendingUpload.objects.create(intake_nonce=nonce, session_digest=binding, original_name='plans.pdf',
            storage_key='ready/form.pdf', size_bytes=20, content_type='application/pdf', state='ready',
            expires_at=timezone.now()+timedelta(hours=1))
        data = {'action': 'publish', 'version': 1, 'intake_token': token, 'upload_ids': str(upload.pk),
                'title': 'Published through form', 'kind': 'plan', 'confirm': 'on'}
        self.assertRedirects(self.client.post(url, data), url)
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(PortalDocument.objects.get().revisions.count(), 1)

    def test_financial_access_downgrade_removes_proposal_immediately(self):
        _, revision = self.sent_proposal()
        self.grant(financial=True)
        self.sign_in()
        url = reverse('workqueue:portal_proposal', args=[self.project.pk, revision.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.grant(financial=False)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_invoice_links_cannot_bypass_portal_financial_permissions(self):
        with self.assertRaisesMessage(ValidationError, 'Upload the invoice PDF'):
            self.publish(kind='invoice', client=self.contact.client, url='https://drive.google.com/file/d/abc/view')
        self.assertFalse(PortalDocument.objects.exists())

    def test_malformed_staff_document_identifier_is_handled(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('workqueue:portal_manage', args=[self.project.pk]),
            {'action': 'withdraw', 'version': 1, 'document': 'not-a-uuid'})
        self.assertEqual(response.status_code, 400)

    def test_contact_archiving_does_not_allow_new_grant(self):
        self.contact.archived_at = timezone.now()
        self.contact.save(update_fields=['archived_at'])
        with self.assertRaises(ValidationError):
            self.grant()

    def test_historical_invoices_require_client_project_and_applied_import(self):
        batch = InvoiceCSVImport.objects.create(actor=self.owner, account_id='TEST', business_name='Test',
            filename='test.csv', source_hash='0'*64, invoices=[], applied_at=timezone.now())
        def history(number, **kwargs):
            fields = dict(batch=batch, account_id='TEST', number=number, exported_client='Test',
                issued=timezone.localdate(), exported_status='Sent', currency='USD', line_total=123,
                lines=[], fingerprint='0'*64, client=self.contact.client, project=self.project)
            fields.update(kwargs)
            return HistoricalInvoice.objects.create(**fields)
        history('VISIBLE-HISTORY')
        history('SECRET-OTHER-PROJECT', project=self.other.project)
        history('SECRET-OTHER-CLIENT', client=self.other.client_contact.client)
        history('SECRET-DRAFT', exported_status='Draft')
        self.grant(financial=True)
        self.sign_in()
        response = self.client.get(self.project_url())
        self.assertContains(response, 'VISIBLE-HISTORY')
        self.assertNotContains(response, 'SECRET')
        batch.removed_at = timezone.now()
        batch.save(update_fields=['removed_at'])
        self.assertNotContains(self.client.get(self.project_url()), 'VISIBLE-HISTORY')

    def test_crm_merge_does_not_expand_granted_billing_client(self):
        from .crm import merge_clients
        member = self.grant(financial=True)
        original_client = member.client
        merge_clients(source_id=original_client.pk, source_version=original_client.version,
            target_id=self.other.client_contact.client_id, target_version=self.other.client_contact.client.version,
            actor=self.owner)
        self.invoice(client=self.other.client_contact.client, invoice_number='SECRET-MERGED-BILL')
        self.sign_in()
        self.assertNotContains(self.client.get(self.project_url()), 'SECRET-MERGED-BILL')

    def test_portal_responses_are_private_and_not_indexable(self):
        self.grant()
        self.sign_in()
        for url in [reverse('workqueue:portal_home'), self.project_url()]:
            response = self.client.get(url)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('noindex', response['X-Robots-Tag'])

    @override_settings(WORK_INTAKE_ENABLED=False)
    def test_public_portal_respects_client_feature_shutdown(self):
        self.assertEqual(self.client.get(reverse('workqueue:portal_home')).status_code, 404)
