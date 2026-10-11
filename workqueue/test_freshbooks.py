import json
import secrets
import time
import uuid
from datetime import timedelta
from unittest.mock import patch, Mock
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Client, WorkItem, NotificationDelivery
from .pm_models import Proposal
from .pm_services import save_proposal, send_proposal, respond
from .services import create_work, EditConflict
from .tests import work_data
from .test_pm import proposal_data
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink, FreshBooksInvoice
from . import freshbooks as fb


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_PUBLIC_BASE_URL='https://testserver', INTAKE_OWNER_EMAIL='owner@example.invalid',
    FRESHBOOKS_ENABLED=True, FRESHBOOKS_CLIENT_ID='test-client-id', FRESHBOOKS_CLIENT_SECRET='test-client-secret',
    FRESHBOOKS_REDIRECT_URI='https://testserver/work-projects/billing/callback/',
    FRESHBOOKS_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
class FreshBooksTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('fb-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.contact = self.item.client_contact
        self.connection = FreshBooksConnection.objects.create(pk=1, account_id='TEST123', business_name='Test business',
            businesses=[{'account_id':'TEST123', 'name':'Test business'}])
        fb.store_tokens(self.connection, dict(access_token='test-access', refresh_token='test-refresh', expires_in=3600))
        self.client.force_login(self.owner)

    def accepted(self, **changes):
        proposal = save_proposal(self.item.project_id, self.contact.client_id, proposal_data(**changes), uuid.uuid4(), 0, self.owner)
        revision = send_proposal(proposal.pk, 1, self.contact.pk, uuid.uuid4(), self.owner)
        respond(revision.invitation.pk, dict(action='accept', full_name='Test client', agreement=True))
        proposal.refresh_from_db()
        FreshBooksClientLink.objects.get_or_create(client=self.contact.client, account_id='TEST123',
            defaults={'customer_id':7, 'label':'Test client', 'actor':self.owner})
        return proposal

    def queued(self):
        proposal = self.accepted()
        return fb.queue_invoice(proposal.pk, proposal.version, self.owner, confirm=True)

    def remote(self, local, **changes):
        data = dict(invoiceid=42, invoice_number='INV-42', customerid=7, po_number=local.marker,
            currency_code='USD', v3_status='draft', amount={'amount':'156.88'}, paid={'amount':'0.00'}, outstanding={'amount':'156.88'})
        data.update(changes)
        return data

    def test_tokens_encrypted_and_unexpired_not_refreshed(self):
        self.assertNotIn('test-access', self.connection.encrypted_tokens)
        self.assertNotIn('test-refresh', self.connection.encrypted_tokens)
        with patch('workqueue.freshbooks.token_exchange') as exchange:
            self.assertEqual(fb.access_token(), 'test-access')
        exchange.assert_not_called()

    def test_expired_pair_rotated_and_persisted_once(self):
        self.connection.expires_at = timezone.now()-timedelta(seconds=1); self.connection.save()
        with patch('workqueue.freshbooks.token_exchange', return_value=dict(access_token='new-access', refresh_token='new-refresh', expires_in=3600)) as exchange:
            self.assertEqual(fb.access_token(), 'new-access')
            self.assertEqual(fb.access_token(), 'new-access')
        exchange.assert_called_once_with(grant_type='refresh_token', refresh_token='test-refresh')
        self.connection.refresh_from_db()
        self.assertEqual(json.loads(fb.cipher().decrypt(self.connection.encrypted_tokens.encode()))['refresh'], 'new-refresh')

    def test_ambiguous_refresh_requires_reconnect_and_never_replays(self):
        self.connection.expires_at = timezone.now()-timedelta(seconds=1); self.connection.save()
        with patch('workqueue.freshbooks.token_exchange', side_effect=fb.BillingError()) as exchange:
            for _ in range(2):
                with self.assertRaises(fb.BillingError):
                    fb.access_token()
        self.assertEqual(exchange.call_count, 1)
        self.connection.refresh_from_db(); self.assertTrue(self.connection.needs_reconnect)

    def test_wire_fixed_host_no_redirects_and_redacted_errors(self):
        response = Mock(status_code=200); response.json.return_value={'response':{}}
        with patch('workqueue.freshbooks.requests.request', return_value=response) as send:
            fb.wire('GET', '/auth/api/v1/users/me', token='safe-test-token')
            self.assertEqual(send.call_args.args[1], 'https://api.freshbooks.com/auth/api/v1/users/me')
            self.assertFalse(send.call_args.kwargs['allow_redirects'])
            with self.assertRaises(fb.BillingError):
                fb.wire('GET', 'https://evil.invalid/')
        response.status_code = 403; response.json.return_value={'secret':'PROVIDER-SECRET'}
        with patch('workqueue.freshbooks.requests.request', return_value=response):
            with self.assertRaises(fb.BillingError) as caught:
                fb.wire('GET', '/private')
        self.assertNotIn('PROVIDER-SECRET', str(caught.exception))

    def test_oauth_callback_requires_owner_state_ttl_and_single_use(self):
        with patch('workqueue.freshbooks.connect') as connect:
            response = self.client.get(reverse('workqueue:freshbooks_callback'), {'code':'not-a-real-code', 'state':'bad'})
            connect.assert_not_called()
            self.assertEqual(response.status_code, 302)
            session = self.client.session
            session['freshbooks_oauth'] = {'state':'test-state', 'time':time.time(), 'actor':self.owner.pk}; session.save()
            self.client.get(reverse('workqueue:freshbooks_callback'), {'code':'not-a-real-code', 'state':'test-state'})
            connect.assert_called_once_with('not-a-real-code', self.owner)
            self.client.get(reverse('workqueue:freshbooks_callback'), {'code':'not-a-real-code', 'state':'test-state'})
            self.assertEqual(connect.call_count, 1)
        staff = get_user_model().objects.create_user('non-owner', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'), Permission.objects.get(codename='change_workitem'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('workqueue:freshbooks_connection')).status_code, 403)

    def test_choose_business_only_from_authorized_list(self):
        with self.assertRaises(ValidationError):
            fb.choose_business('OTHER123', self.owner)
        fb.choose_business('TEST123', self.owner)
        local = self.queued()
        fb.choose_business('TEST123', self.owner)
        self.connection.businesses.append({'account_id':'OTHER123', 'name':'Other test business'}); self.connection.save()
        with self.assertRaises(ValidationError):
            fb.choose_business('OTHER123', self.owner)

    def test_exact_email_search_and_no_client_creation(self):
        with patch('workqueue.freshbooks.api', return_value={'response':{'result':{'clients':[]}}}) as api:
            fb.find_clients('client@example.invalid')
        self.assertEqual(api.call_args.args[0], 'GET')
        self.assertEqual(api.call_args.kwargs['params']['search[email]'], 'client@example.invalid')

    def test_client_connection_unique_without_overwriting_contact(self):
        remote = dict(id=7, fname='Test', lname='Person', email='billing@example.invalid', vis_state=0)
        with patch('workqueue.freshbooks.remote_client', return_value=remote):
            fb.link_client(self.contact.client, 7, self.owner)
            other = Client.objects.create(name='Another company', normalized_name='another company')
            with self.assertRaises(ValidationError):
                fb.link_client(other, 7, self.owner)
        self.contact.refresh_from_db(); self.assertEqual(self.contact.email, 'morgan@example.com')
        self.assertEqual(FreshBooksClientLink.objects.count(), 1)

    def test_invoice_requires_accepted_fixed_price_explicit_consent_and_client_link(self):
        proposal = self.accepted()
        with self.assertRaises(ValidationError):
            fb.queue_invoice(proposal.pk, proposal.version, self.owner)
        FreshBooksClientLink.objects.all().delete()
        with self.assertRaises(ValidationError):
            fb.queue_invoice(proposal.pk, proposal.version, self.owner, confirm=True)
        hourly = self.accepted(pricing='hourly')
        with self.assertRaises(ValidationError):
            fb.queue_invoice(hourly.pk, hourly.version, self.owner, confirm=True)

    def test_invoice_queue_idempotent_frozen_payload_and_no_side_effects(self):
        proposal = self.accepted()
        before = list(WorkItem.objects.values_list('pk', 'status', 'queue_order'))
        notices = NotificationDelivery.objects.count()
        with patch('workqueue.freshbooks.wire') as api:
            one = fb.queue_invoice(proposal.pk, proposal.version, self.owner, confirm=True)
            two = fb.queue_invoice(proposal.pk, proposal.version, self.owner, confirm=True)
        api.assert_not_called()
        self.assertEqual(one.pk, two.pk)
        self.assertEqual(one.payload['invoice']['customerid'], 7)
        self.assertNotIn('action_email', one.payload['invoice'])
        self.assertNotIn('status', one.payload['invoice'])
        self.assertEqual(before, list(WorkItem.objects.values_list('pk', 'status', 'queue_order')))
        self.assertEqual(notices, NotificationDelivery.objects.count())

    def test_create_draft_and_copy_only_configured_payment_options(self):
        local = self.queued()
        with patch('workqueue.freshbooks.remote_client', return_value={'id':7}), patch('workqueue.freshbooks.wire', return_value={'response':{'result':{'invoice':self.remote(local)}}}) as wire, patch('workqueue.freshbooks.api') as api:
            api.return_value = {'payment_options':{'gateway_name':'stripe', 'has_credit_card':True, 'has_ach_transfer':False, 'gateway_info':{'secret':'private'}}}
            fb.process_invoice(local.pk)
        local.refresh_from_db()
        self.assertEqual(local.state, 'linked'); self.assertEqual(local.invoice_id, 42)
        self.assertTrue(local.payment_options_applied)
        posted = api.call_args.kwargs['data']
        self.assertEqual(posted['gateway_name'], 'stripe')
        self.assertNotIn('gateway_info', posted)
        self.assertEqual(wire.call_args.args[0], 'POST')

    def test_unknown_post_held_and_not_retried(self):
        local = self.queued()
        with patch('workqueue.freshbooks.remote_client', return_value={'id':7}), patch('workqueue.freshbooks.wire', side_effect=fb.BillingError()) as wire:
            fb.process_invoice(local.pk)
            fb.process_invoice(local.pk)
        self.assertEqual(wire.call_count, 1)
        local.refresh_from_db(); self.assertEqual(local.state, 'unknown')

    def test_rejected_post_failed_not_unknown(self):
        local = self.queued()
        with patch('workqueue.freshbooks.remote_client', return_value={'id':7}), patch('workqueue.freshbooks.wire', side_effect=fb.BillingError(status=422)):
            fb.process_invoice(local.pk)
        local.refresh_from_db(); self.assertEqual(local.state, 'failed')

    def test_payment_setup_failure_never_recreates_invoice(self):
        local = self.queued()
        with patch('workqueue.freshbooks.remote_client', return_value={'id':7}), patch('workqueue.freshbooks.wire', return_value={'response':{'result':{'invoice':self.remote(local)}}}) as wire, patch('workqueue.freshbooks.api', side_effect=fb.BillingError('Review payment options.')):
            fb.process_invoice(local.pk)
            fb.process_invoice(local.pk)
        self.assertEqual(wire.call_count, 1)
        local.refresh_from_db(); self.assertEqual(local.state, 'linked')
        self.assertIn('Review payment', local.last_error)

    def test_unknown_reconcile_exact_marker_client_total_only(self):
        local = self.queued(); local.state='unknown'; local.save()
        remote = self.remote(local)
        with patch('workqueue.freshbooks.api', return_value={'response':{'result':{'invoices':[remote]}}}) as api:
            fb.sync_invoice(local)
        self.assertEqual(local.invoice_id, 42)
        self.assertEqual(api.call_args.args[0], 'GET')
        other = self.queued()
        with patch('workqueue.freshbooks.api', return_value={'response':{'result':{'invoices':[self.remote(other, customerid=999)]}}}):
            with self.assertRaises(fb.BillingError):
                fb.sync_invoice(other)
        other.refresh_from_db(); self.assertIsNone(other.invoice_id)

    def test_disabled_billing_worker_no_network(self):
        with override_settings(FRESHBOOKS_ENABLED=False), patch('workqueue.freshbooks.api') as api:
            self.assertEqual(fb.sync_billing(), 0)
        api.assert_not_called()

    def test_known_rejection_retry_checks_existing_invoice_and_never_resets_unknown(self):
        local = self.queued(); local.state='failed'; local.save()
        with patch('workqueue.freshbooks.api', return_value={'response':{'result':{'invoices':[]}}}):
            with self.assertRaises(ValidationError):
                fb.retry_rejected_invoice(local.pk, self.owner)
            fb.retry_rejected_invoice(local.pk, self.owner, confirm=True)
        local.refresh_from_db(); self.assertEqual(local.state, 'queued')
        local.state='unknown'; local.save()
        with self.assertRaises(ValidationError):
            fb.retry_rejected_invoice(local.pk, self.owner, confirm=True)
        local.state='failed'; local.save()
        with patch('workqueue.freshbooks.api', return_value={'response':{'result':{'invoices':[self.remote(local)]}}}):
            with self.assertRaises(ValidationError):
                fb.retry_rejected_invoice(local.pk, self.owner, confirm=True)

    def test_billing_status_view_preserves_job_and_shows_balances(self):
        local = self.queued(); fb.save_remote(local, self.remote(local))
        response = self.client.get(reverse('workqueue:proposal_detail', args=[local.proposal_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INV-42')
        self.assertContains(response, '156.88')
        self.assertContains(response, 'Refresh billing status')
