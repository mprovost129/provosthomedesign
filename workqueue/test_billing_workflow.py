import uuid
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError,transaction
from django.test import TestCase,TransactionTestCase,override_settings,skipUnlessDBFeature
from django.urls import reverse
from .models import Client,ClientContact,WorkItem,NotificationDelivery
from .services import create_work,EditConflict
from .tests import work_data
from .crm import save_contact,archive_contact,restore_contact,merge_clients
from .shared_contact_views import share_contact
from .time_models import TimeEntry
from .time_services import save_time,archive_time
from .time_billing import eligible,preview,queue_time_invoice,release_batch
from .time_billing_models import TimeBillingLine,TimeBillingBatch
from .freshbooks_models import FreshBooksConnection,FreshBooksClientLink,FreshBooksInvoice
from . import freshbooks


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class CompanyContactsTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('shared-owner','owner@example.invalid','test')
        self.first=create_work(data=work_data(company='Fed Cap',contact_full_name='Mike'),actor=self.owner)[0]
        self.second=create_work(data=work_data(company='Redtail',contact_full_name='Mike'),actor=self.owner)[0]
        self.client.force_login(self.owner)

    def test_shared_email_keeps_company_jobs_separate_and_repeat_submission_reuses_company_contact(self):
        self.assertNotEqual(self.first.client_contact.client_id,self.second.client_contact.client_id)
        self.assertNotEqual(self.first.client_contact_id,self.second.client_contact_id)
        repeat=create_work(data=work_data(company='Fed Cap',contact_full_name='Mike'),actor=self.owner)[0]
        self.assertEqual(repeat.client_contact_id,self.first.client_contact_id)
        self.assertEqual(ClientContact.objects.filter(email='morgan@example.com').count(),2)

    def test_same_company_name_different_email_groups_contacts_without_public_access(self):
        item=create_work(data=work_data(company='Fed Cap',contact_email='another@example.invalid'),actor=self.owner)[0]
        self.assertEqual(item.client_contact.client_id,self.first.client_contact.client_id)
        self.assertNotEqual(item.client_contact_id,self.first.client_contact_id)

    def test_same_email_uniqueness_is_per_company(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            ClientContact.objects.create(client=self.first.client_contact.client,email='morgan@example.com')

    def test_explicit_link_preserves_companies_work_and_profile_details(self):
        a,b=self.first.client_contact.client,self.second.client_contact.client
        before=(Client.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count())
        linked=share_contact(a.pk,self.first.client_contact_id,b.pk,a.version,b.version,self.owner)
        self.assertEqual(linked.pk,self.second.client_contact_id);linked.refresh_from_db()
        self.assertEqual(linked.linked_contact_id,self.first.client_contact_id)
        self.assertEqual(before,(Client.objects.count(),WorkItem.objects.count(),NotificationDelivery.objects.count()))
        self.first.refresh_from_db();self.second.refresh_from_db()
        self.assertNotEqual(self.first.client_contact.client_id,self.second.client_contact.client_id)
        response=self.client.get(reverse('workqueue:client_detail',args=[b.pk]))
        self.assertContains(response,'Also a contact for:');self.assertContains(response,'Fed Cap')

    def test_link_stale_versions_and_wrong_source_are_rejected(self):
        a,b=self.first.client_contact.client,self.second.client_contact.client
        with self.assertRaises(EditConflict):share_contact(a.pk,self.first.client_contact_id,b.pk,a.version+1,b.version,self.owner)
        with self.assertRaises(EditConflict):share_contact(a.pk,self.second.client_contact_id,b.pk,a.version,b.version,self.owner)

    def test_archiving_and_restoring_one_company_contact_does_not_change_other(self):
        a=self.first.client_contact.client
        archive_contact(client_id=a.pk,version=a.version,contact_id=self.first.client_contact_id,actor=self.owner)
        a.refresh_from_db();restore_contact(client_id=a.pk,version=a.version,contact_id=self.first.client_contact_id,actor=self.owner)
        self.second.client_contact.refresh_from_db();self.assertEqual(self.second.client_contact.email,'morgan@example.com')

    def test_merging_shared_email_companies_requires_contact_review_and_is_atomic(self):
        a,b=self.first.client_contact.client,self.second.client_contact.client
        with self.assertRaises(ValidationError):merge_clients(source_id=a.pk,source_version=a.version,target_id=b.pk,target_version=b.version,actor=self.owner)
        a.refresh_from_db();self.assertIsNone(a.merged_into_id)

    def test_shared_company_route_confirmation_signature_and_private_permissions(self):
        a,b=self.first.client_contact.client,self.second.client_contact.client
        url=reverse('workqueue:link_contact_company',args=[a.pk,self.first.client_contact_id])
        response=self.client.get(url);token=response.context['form'].initial['signature']
        self.assertEqual(self.client.post(url,{'company':b.pk,'signature':token}).status_code,400)
        self.assertEqual(self.client.post(url,{'company':b.pk,'signature':'bad','confirm':'on'}).status_code,400)
        self.assertEqual(self.client.post(url,{'company':b.pk,'signature':token,'confirm':'on'}).status_code,302)
        reader=get_user_model().objects.create_user('shared-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(reader);self.assertEqual(self.client.get(url).status_code,403)


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class TimeBillingTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('time-bill-owner','owner@example.invalid','test')
        config=patch('workqueue.freshbooks.configured',return_value=True);config.start();self.addCleanup(config.stop)
        self.item=create_work(data=work_data(),actor=self.owner)[0];self.company=self.item.client_contact.client
        self.connection=FreshBooksConnection.objects.create(pk=1,account_id='TEST123',encrypted_tokens='fake-test-only')
        FreshBooksClientLink.objects.create(client=self.company,account_id='TEST123',customer_id=7,label='Test')
        self.entry=TimeEntry.objects.create(project=self.item.project,work_item=self.item,actor=self.owner,duration_seconds=4800,description='Design review')
        self.client.force_login(self.owner)

    def snapshot(self,entries=None):
        records=list(eligible(self.item.project).filter(pk__in=[e.pk for e in entries])) if entries else list(eligible(self.item.project))
        return preview(self.item.project,self.company,records,Decimal('75'),{str(e.pk):'Reviewed public description' for e in records},'Public note')

    def queue(self):return queue_time_invoice(self.snapshot(),uuid.uuid4(),self.owner)

    def remote(self,invoice):
        return {'invoiceid':99,'invoice_number':'TEST-HOURLY','customerid':7,'po_number':invoice.marker,
            'currency_code':'USD','v3_status':'draft','amount':{'amount':'100.00'},'paid':{'amount':'0'},'outstanding':{'amount':'100.00'}}

    def test_preview_uses_decimal_per_line_rounding_and_no_external_calls_or_writes(self):
        with patch('workqueue.freshbooks.api') as api:
            before=(FreshBooksInvoice.objects.count(),TimeBillingBatch.objects.count(),NotificationDelivery.objects.count())
            result=self.snapshot()
        api.assert_not_called();self.assertEqual(result['total'],'100.00');self.assertEqual(result['rows'][0]['quantity'],'1.333333')
        self.assertEqual(before,(FreshBooksInvoice.objects.count(),TimeBillingBatch.objects.count(),NotificationDelivery.objects.count()))
        self.assertNotIn(self.item.internal_notes,str(result)) if self.item.internal_notes else None

    def test_queued_hours_reserved_once_and_retry_returns_same_request(self):
        data=self.snapshot();nonce=uuid.uuid4()
        batch=queue_time_invoice(data,nonce,self.owner)
        again=queue_time_invoice(data,nonce,self.owner)
        self.assertEqual(batch.pk,again.pk);self.assertEqual(FreshBooksInvoice.objects.count(),1)
        self.assertEqual(TimeBillingLine.objects.filter(active=True).count(),1);self.assertFalse(eligible(self.item.project).exists())
        with self.assertRaises((ValidationError,EditConflict)):queue_time_invoice(data,uuid.uuid4(),self.owner)

    def test_time_changes_or_company_billing_link_changes_reject_stale_preview(self):
        data=self.snapshot();TimeEntry.objects.filter(pk=self.entry.pk).update(version=2,duration_seconds=5000)
        with self.assertRaises(EditConflict):queue_time_invoice(data,uuid.uuid4(),self.owner)
        self.assertFalse(TimeBillingBatch.objects.exists())
        data=self.snapshot();FreshBooksClientLink.objects.update(customer_id=8)
        with self.assertRaises(EditConflict):queue_time_invoice(data,uuid.uuid4(),self.owner)

    def test_reserved_time_cannot_be_edited_removed_or_rebilled(self):
        self.queue()
        with self.assertRaises(ValidationError):archive_time(self.entry.project_id,self.entry.pk,self.entry.version,self.owner)
        data={'token':self.entry.pk,'version':self.entry.version,'work_date':self.entry.work_date,'minutes':90,
              'work_item':self.item,'category':'design','description':'Change','billable':True}
        with self.assertRaises(ValidationError):save_time(self.entry.project_id,data,self.owner)
        self.entry.refresh_from_db();self.assertEqual(self.entry.duration_seconds,4800)

    def test_running_removed_and_nonbillable_entries_are_not_available(self):
        TimeEntry.objects.create(project=self.item.project,actor=self.owner,source='timer',started_at=freshbooks.timezone.now())
        TimeEntry.objects.create(project=self.item.project,actor=self.owner,duration_seconds=60,billable=False)
        TimeEntry.objects.create(project=self.item.project,actor=self.owner,duration_seconds=60,deleted_at=freshbooks.timezone.now())
        self.assertEqual(eligible(self.item.project).count(),1)

    def test_cross_company_time_cannot_be_billed_to_other_company(self):
        other=create_work(data=work_data(company='Other Company'),actor=self.owner)[0]
        self.entry.work_item=other;self.entry.save()
        with self.assertRaises(ValidationError):self.snapshot()

    def test_release_known_uncreated_request_reopens_time_without_deleting_history(self):
        batch=self.queue();release_batch(batch.pk,self.owner);release_batch(batch.pk,self.owner)
        self.assertEqual(eligible(self.item.project).count(),1);self.assertEqual(TimeBillingLine.objects.count(),1)
        batch.invoice.refresh_from_db();self.assertEqual(batch.invoice.state,'released')

    def test_uncertain_creating_and_created_invoices_never_release_time(self):
        batch=self.queue()
        for state,id in [('creating',None),('unknown',None),('linked',99)]:
            FreshBooksInvoice.objects.filter(pk=batch.invoice_id).update(state=state,invoice_id=id)
            with self.assertRaises(ValidationError):release_batch(batch.pk,self.owner)
            self.assertFalse(eligible(self.item.project).exists())

    def test_provider_create_runs_once_unsent_and_refresh_reconciles_marker(self):
        batch=self.queue();invoice=batch.invoice
        with patch('workqueue.freshbooks.access_token',return_value='fake'),patch('workqueue.freshbooks.remote_client'),\
             patch('workqueue.freshbooks.wire',return_value={'response':{'result':{'invoice':self.remote(invoice)}}}) as wire,\
             patch('workqueue.freshbooks.copy_payment_options'):
            freshbooks.process_invoice(invoice.pk);freshbooks.process_invoice(invoice.pk)
        self.assertEqual(wire.call_count,1);payload=wire.call_args.kwargs['data']['invoice']
        self.assertNotIn('action_email',payload);self.assertNotIn('action_mark_as_sent',payload)
        invoice.refresh_from_db();self.assertEqual(invoice.state,'linked');self.assertEqual(invoice.invoice_id,99)
        self.assertFalse(eligible(self.item.project).exists())

    def test_timeout_holds_reservation_and_reconciliation_prevents_duplicate_create(self):
        batch=self.queue();invoice=batch.invoice
        with patch('workqueue.freshbooks.access_token',return_value='fake'),patch('workqueue.freshbooks.remote_client'),\
             patch('workqueue.freshbooks.wire',side_effect=freshbooks.BillingError('Timeout')):
            freshbooks.process_invoice(invoice.pk)
        invoice.refresh_from_db();self.assertEqual(invoice.state,'unknown')
        with self.assertRaises(ValidationError):release_batch(batch.pk,self.owner)
        with patch('workqueue.freshbooks.api',return_value={'response':{'result':{'invoices':[self.remote(invoice)]}}}):freshbooks.sync_invoice(invoice)
        invoice.refresh_from_db();self.assertEqual(invoice.state,'linked');self.assertEqual(FreshBooksInvoice.objects.count(),1)

    def test_definitive_rejection_can_release_but_changed_billing_client_cannot_create(self):
        batch=self.queue();FreshBooksClientLink.objects.update(customer_id=8)
        with patch('workqueue.freshbooks.wire') as wire:freshbooks.process_invoice(batch.invoice_id)
        wire.assert_not_called();batch.invoice.refresh_from_db();self.assertEqual(batch.invoice.state,'failed')
        release_batch(batch.pk,self.owner);self.assertTrue(eligible(self.item.project).exists())

    def test_fixed_price_scope_requires_additional_work_confirmation(self):
        from .pm_models import Proposal,ProposalRevision
        proposal=Proposal.objects.create(project=self.item.project,client=self.company,number='FIXED',title='Fixed',has_draft=False)
        revision=ProposalRevision.objects.create(proposal=proposal,sequence=1,state='accepted',recipient='test@example.invalid',snapshot={'pricing':'fixed'})
        proposal.current_revision=revision;proposal.save()
        data=self.snapshot()
        with self.assertRaises(ValidationError):queue_time_invoice(data,uuid.uuid4(),self.owner)
        self.assertFalse(FreshBooksInvoice.objects.exists())
        self.assertTrue(queue_time_invoice(data,uuid.uuid4(),self.owner,additional=True).invoice_id)

    def test_preview_confirm_route_signed_actor_bound_and_duplicate_post_safe(self):
        url=reverse('workqueue:project_billing',args=[self.item.project_id])
        response=self.client.post(url,{'action':'preview','client':self.company.pk,'rate':'75','entries':[self.entry.pk],
                                      'description_'+str(self.entry.pk):'Public design work'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['review']['total'],'100.00')
        token=response.context['signature']
        self.assertEqual(self.client.post(url,{'action':'confirm','signature':token}).status_code,400)
        self.assertEqual(self.client.post(url,{'action':'confirm','signature':'bad','confirm':'yes'}).status_code,400)
        for _ in range(2):self.assertEqual(self.client.post(url,{'action':'confirm','signature':token,'confirm':'yes'}).status_code,302)
        self.assertEqual(FreshBooksInvoice.objects.count(),1)

    def test_hourly_invoices_render_in_overview_and_reports_without_fake_proposals(self):
        batch=self.queue()
        self.assertContains(self.client.get(reverse('workqueue:invoice_overview')),'Hourly billing')
        self.assertEqual(self.client.get(reverse('workqueue:project_reports')).status_code,200)
        self.assertEqual(self.client.get(reverse('workqueue:time_bill_detail',args=[batch.pk])).status_code,200)
        self.assertEqual(self.item.project.proposals.count(),0)

    def test_staff_readonly_feature_gate_csrf_and_private_cache(self):
        from django.test import Client as TestBrowser
        secure=TestBrowser(enforce_csrf_checks=True);secure.force_login(self.owner)
        url=reverse('workqueue:project_billing',args=[self.item.project_id])
        self.assertEqual(secure.post(url,{'action':'preview'}).status_code,403)
        reader=get_user_model().objects.create_user('bill-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'));self.client.force_login(reader)
        self.assertEqual(self.client.post(url,{'action':'preview'}).status_code,403)
        response=self.client.get(url);self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control'])
        self.assertNotContains(response,'Preview invoice')
        with override_settings(WORK_QUEUE_ENABLED=False):self.assertEqual(self.client.get(url).status_code,404)

    def test_disabled_provider_cannot_reserve_time(self):
        with patch('workqueue.freshbooks.configured',return_value=False):
            with self.assertRaises(ValidationError):self.queue()
        self.assertFalse(TimeBillingLine.objects.exists())

    def test_reserved_time_screen_explains_lock_and_hides_edit_actions(self):
        self.queue()
        url=reverse('workqueue:project_time',args=[self.item.project_id])
        self.assertContains(self.client.get(url),'Reserved for invoice')
        self.assertNotContains(self.client.get(url),'Edit time')
        response=self.client.get(url+'?entry='+str(self.entry.pk))
        self.assertContains(response,'This time is reserved for an invoice.')
        self.assertNotContains(response,'Save correction')


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class TimeBillingConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_invoice_confirmations_reserve_a_time_entry_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        owner=get_user_model().objects.create_superuser('race-bill-owner','owner@example.invalid','test')
        item=create_work(data=work_data(),actor=owner)[0];company=item.client_contact.client
        FreshBooksConnection.objects.create(pk=1,account_id='TEST123',encrypted_tokens='fake-test-only')
        FreshBooksClientLink.objects.create(client=company,account_id='TEST123',customer_id=7,label='Test')
        entry=TimeEntry.objects.create(project=item.project,work_item=item,actor=owner,duration_seconds=3600)
        with patch('workqueue.freshbooks.configured',return_value=True):
            data=preview(item.project,company,list(eligible(item.project)),Decimal('75'),{str(entry.pk):'Reviewed design work'},'')
            def confirm(_):
                close_old_connections()
                try:
                    actor=get_user_model().objects.get(pk=owner.pk)
                    queue_time_invoice(data,uuid.uuid4(),actor);return 'created'
                except (ValidationError,EditConflict):return 'conflict'
                finally:close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(confirm,range(2)))
        self.assertEqual(sorted(results),['conflict','created'])
        self.assertEqual(FreshBooksInvoice.objects.count(),1);self.assertEqual(TimeBillingLine.objects.filter(active=True).count(),1)
