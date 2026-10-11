import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from .services import create_work
from .tests import work_data
from .test_pm import proposal_data
from .pm_services import save_proposal, send_proposal, respond
from .models import NotificationDelivery, QueueState
from .freshbooks_models import FreshBooksConnection, FreshBooksClientLink, FreshBooksInvoice
from . import freshbooks as fb


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_PUBLIC_BASE_URL='https://testserver', INTAKE_OWNER_EMAIL='owner@example.invalid',
    FRESHBOOKS_ENABLED=True, FRESHBOOKS_CLIENT_ID='test-id', FRESHBOOKS_CLIENT_SECRET='test-secret',
    FRESHBOOKS_REDIRECT_URI='https://testserver/work-projects/billing/callback/',
    FRESHBOOKS_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
class ProjectConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_superuser('pm-race-owner', 'owner@example.invalid', 'test-only')
        self.item = create_work(data=work_data(), actor=self.owner)[0]
        self.proposal = save_proposal(self.item.project_id, self.item.client_contact.client_id, proposal_data(), uuid.uuid4(), 0, self.owner)
        self.revision = send_proposal(self.proposal.pk, 1, self.item.client_contact_id, uuid.uuid4(), self.owner)

    def concurrent(self, operation):
        def isolated(_):
            close_old_connections()
            try:
                return operation()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(isolated, range(2)))

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_acceptance_records_one_receipt_pair(self):
        results = self.concurrent(lambda: respond(self.revision.invitation.pk,
            dict(action='accept', full_name='Test Client', agreement=True)).pk)
        self.assertEqual(results[0], results[1])
        self.assertEqual(NotificationDelivery.objects.count(), 3)

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_invoice_requests_create_one_durable_intent(self):
        respond(self.revision.invitation.pk, dict(action='accept', full_name='Test', agreement=True))
        self.proposal.refresh_from_db()
        FreshBooksConnection.objects.create(pk=1, account_id='TEST123', encrypted_tokens='test-only-not-used')
        FreshBooksClientLink.objects.create(client=self.item.client_contact.client, account_id='TEST123', customer_id=7, label='Test')
        results = self.concurrent(lambda: fb.queue_invoice(self.proposal.pk, self.proposal.version, self.owner, confirm=True).pk)
        self.assertEqual(results[0], results[1]); self.assertEqual(FreshBooksInvoice.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_refresh_uses_rotating_token_once(self):
        connection = FreshBooksConnection.objects.create(pk=1)
        fb.store_tokens(connection, dict(access_token='expired-access', refresh_token='once-only', expires_in=1))
        connection.expires_at = timezone.now()-timedelta(seconds=1); connection.save()
        def exchange(**kwargs):
            time.sleep(0.05)
            return dict(access_token='new-access', refresh_token='rotated-once', expires_in=3600)
        with patch('workqueue.freshbooks.token_exchange', side_effect=exchange) as refresh:
            self.assertEqual(self.concurrent(fb.access_token), ['new-access', 'new-access'])
        self.assertEqual(refresh.call_count, 1)
        connection.refresh_from_db()
        self.assertEqual(json.loads(fb.cipher().decrypt(connection.encrypted_tokens.encode()))['refresh'], 'rotated-once')
