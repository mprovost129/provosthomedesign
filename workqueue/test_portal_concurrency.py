from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from .portal_models import PortalMember, PortalDocumentVersion
from .models import QueueState
from .portal_services import grant_member, publish_document
from .services import EditConflict, create_work
from .tests import work_data


@skipUnlessDBFeature('has_select_for_update')
class PortalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)
        self.actor = get_user_model().objects.create_superuser('portal-concurrency', password=None)
        self.item = create_work(data=work_data(), actor=self.actor)[0]

    def race(self, action):
        def run(index):
            close_old_connections()
            try:
                action(index)
                return 'saved'
            except EditConflict:
                return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, [0, 1]))
        self.assertCountEqual(results, ['saved', 'conflict'])

    def test_two_permission_edits_cannot_silently_overwrite_each_other(self):
        self.race(lambda index: grant_member(self.item.project, version=1, contact_id=self.item.client_contact_id,
            financial_access=bool(index), actor=self.actor))
        self.assertEqual(PortalMember.objects.count(), 1)

    def test_duplicate_publication_creates_only_one_version(self):
        self.race(lambda index: publish_document(self.item.project, version=1, title='Plan link', kind='plan',
            billing_client_id=None, document_id=None, note='', external_url='https://drive.google.com/file/d/abc/view',
            upload_ids='', nonce=None, binding='', actor=self.actor))
        self.assertEqual(PortalDocumentVersion.objects.count(), 1)
