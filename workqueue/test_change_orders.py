import copy
import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,TransactionTestCase,Client as Browser,override_settings,skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .models import WorkItem,NotificationDelivery
from .pm_models import Proposal,ProposalRevision
from .pm_services import save_proposal,send_proposal,respond,revise_proposal,withdraw_proposal,snapshot_for
from .change_orders import save_change_order,amounts
from .services import create_work,EditConflict
from .test_pm import proposal_data
from .tests import work_data


def change_data(**changes):
    data=proposal_data(document_kind='change_order',document_style='phd-studio-v1',title='Additional kitchen revisions',
        scope='Revise the kitchen after the approved plan.',deliverables='Updated plan PDF',schedule_affected='yes',additional_days=3,
        proposed_completion_date='',timeline='Allow three additional calendar days.',
        issue_date=timezone.localdate().isoformat(),lines=[{'description':'Additional design','quantity':'2','rate':'125'}])
    data.update(changes);return data


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False,
    INTAKE_PUBLIC_BASE_URL='https://testserver',INTAKE_OWNER_EMAIL='owner@example.invalid')
class ChangeOrderTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('change-owner','owner@example.invalid','test-only')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.contact=self.item.client_contact;self.account=self.contact.client
        self.base_proposal=save_proposal(self.item.project_id,self.account.pk,proposal_data(lines=[{'description':'Original design','quantity':'1','rate':'2000'}]),uuid.uuid4(),0,self.owner)
        self.base=send_proposal(self.base_proposal.pk,1,self.contact.pk,uuid.uuid4(),self.owner)
        respond(self.base.invitation.pk,{'action':'accept','full_name':'Example client','agreement':True})
        self.base.refresh_from_db();self.base_proposal.refresh_from_db()
        self.frozen=copy.deepcopy(self.base.snapshot)
        self.client.force_login(self.owner)

    def draft(self,**changes):
        return save_change_order(self.base.pk,change_data(**changes),uuid.uuid4(),0,self.owner)

    def sent(self,**changes):
        draft=self.draft(**changes)
        revision=send_proposal(draft.pk,draft.version,self.contact.pk,uuid.uuid4(),self.owner)
        draft.refresh_from_db();return draft,revision

    def post_data(self,**changes):
        data=change_data();data.update(client=str(self.account.pk),token=str(uuid.uuid4()),version='0',
            **{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-description':'Additional design',
            'lines-0-quantity':'2','lines-0-rate':'125','lines-0-ORDER':'0'})
        data.update(changes);return data

    def test_staff_draft_links_to_exact_accepted_version_without_notifications_or_queue_edits(self):
        before=list(WorkItem.objects.values_list('pk','status','queue_order'));notices=NotificationDelivery.objects.count()
        draft=self.draft();snapshot=snapshot_for(draft)
        self.assertEqual(draft.base_revision_id,self.base.pk)
        self.assertEqual(draft.number,self.base_proposal.number+'-CO-01')
        self.assertEqual(snapshot['total'],'250.00')
        self.assertEqual(snapshot['change_order']['revised_total'],'2250.00')
        self.assertEqual(draft.document_label,'Change order')
        self.assertEqual(before,list(WorkItem.objects.values_list('pk','status','queue_order')))
        self.assertEqual(notices,NotificationDelivery.objects.count())
        self.base.refresh_from_db();self.assertEqual(self.base.snapshot,self.frozen)

    def test_send_and_accept_freeze_scope_price_and_schedule_and_notify_once(self):
        draft,revision=self.sent();frozen=copy.deepcopy(revision.snapshot)
        self.assertIn('change order',revision.invitation.notification.body)
        self.assertIn('Revised agreement total: $2250.00',revision.invitation.notification.body)
        before=NotificationDelivery.objects.count()
        payload={'action':'accept','full_name':'Example client','agreement':True,'note':'Approved'}
        respond(revision.invitation.pk,payload);respond(revision.invitation.pk,payload)
        self.assertEqual(NotificationDelivery.objects.count(),before+2)
        revision.refresh_from_db();self.assertEqual(revision.snapshot,frozen)
        self.assertEqual(revision.snapshot['additional_days'],3)
        self.assertEqual(amounts(self.base)['approved_total'],'2250.00')
        self.base.refresh_from_db();self.assertEqual(self.base.snapshot,self.frozen)

    def test_only_accepted_current_root_agreements_can_start_changes(self):
        draft=save_proposal(self.item.project_id,self.account.pk,proposal_data(),uuid.uuid4(),0,self.owner)
        sent=send_proposal(draft.pk,1,self.contact.pk,uuid.uuid4(),self.owner)
        with self.assertRaises(ValidationError):save_change_order(sent.pk,change_data(),uuid.uuid4(),0,self.owner)
        self.assertEqual(self.client.get(reverse('workqueue:change_order_new',args=[sent.pk])).status_code,409)
        child,revision=self.sent();respond(revision.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})
        with self.assertRaises(ValidationError):save_change_order(revision.pk,change_data(),uuid.uuid4(),0,self.owner)

    def test_draft_idempotency_version_and_base_identity_checks(self):
        token=uuid.uuid4();draft=save_change_order(self.base.pk,change_data(),token,0,self.owner)
        self.assertEqual(save_change_order(self.base.pk,change_data(title='Duplicate'),token,0,self.owner).pk,draft.pk)
        changed=save_change_order(self.base.pk,change_data(title='Updated'),token,1,self.owner,draft.pk)
        self.assertEqual(changed.version,2)
        with self.assertRaises(EditConflict):save_change_order(self.base.pk,change_data(),token,1,self.owner,draft.pk)
        outsider=get_user_model().objects.create_superuser('other-change-owner','other@example.invalid','test-only')
        with self.assertRaises(ValidationError):save_change_order(self.base.pk,change_data(),token,0,outsider)
        with self.assertRaises(PermissionDenied):save_change_order(self.base.pk,change_data(),uuid.uuid4(),0,None)

    def test_only_one_pending_change_can_be_sent_and_accepted_total_carries_to_next(self):
        first,revision=self.sent();second=self.draft()
        with self.assertRaises(ValidationError):send_proposal(second.pk,1,self.contact.pk,uuid.uuid4(),self.owner)
        respond(revision.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})
        next_revision=send_proposal(second.pk,1,self.contact.pk,uuid.uuid4(),self.owner)
        self.assertEqual(next_revision.snapshot['change_order']['approved_changes'],'250.00')
        self.assertEqual(next_revision.snapshot['change_order']['revised_total'],'2500.00')
        revision.refresh_from_db();self.assertEqual(revision.snapshot['change_order']['revised_total'],'2250.00')

    def test_withdrawing_pending_change_unblocks_next_and_does_not_add_its_price(self):
        first,revision=self.sent();first.refresh_from_db()
        withdraw_proposal(first.pk,first.version,self.owner)
        second=self.draft();revision2=send_proposal(second.pk,1,self.contact.pk,uuid.uuid4(),self.owner)
        self.assertEqual(revision2.snapshot['change_order']['approved_changes'],'0.00')
        with self.assertRaises(ValidationError):respond(revision.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})

    def test_accepted_changes_cannot_be_revised_away_or_base_rewritten(self):
        child,revision=self.sent();respond(revision.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})
        child.refresh_from_db();self.base_proposal.refresh_from_db()
        with self.assertRaises(ValidationError):revise_proposal(child.pk,child.version,self.owner)
        with self.assertRaises(ValidationError):revise_proposal(self.base_proposal.pk,self.base_proposal.version,self.owner)
        self.assertEqual(amounts(self.base)['approved_total'],'2250.00')
        self.assertNotContains(self.client.get(reverse('workqueue:proposal_detail',args=[child.pk])),'Create draft revision')
        self.assertNotContains(self.client.get(reverse('workqueue:proposal_detail',args=[self.base_proposal.pk])),'Create draft revision')

    def test_schedule_requires_explanation_and_no_change_cannot_add_days(self):
        for data in [change_data(timeline=''),change_data(schedule_affected='no'),change_data(additional_days=-1)]:
            with self.assertRaises(ValidationError):save_change_order(self.base.pk,data,uuid.uuid4(),0,self.owner)
        draft=self.draft(schedule_affected='no',additional_days=None,timeline='',proposed_completion_date='')
        self.assertEqual(draft.draft['timeline'],'No change to the current schedule.')

    def test_zero_price_and_hourly_changes_are_explicit_and_do_not_reprice_original(self):
        draft=self.draft(pricing='hourly',lines=[{'description':'Included correction','quantity':'1','rate':'0'}])
        snapshot=snapshot_for(draft)
        self.assertEqual(snapshot['total'],'0.00')
        self.assertTrue(snapshot['change_order']['estimated'])
        self.assertEqual(snapshot['change_order']['revised_total'],'2000.00')
        with self.assertRaises(ValidationError):self.draft(lines=[{'description':'Credit','quantity':'1','rate':'-10'}])

    def test_generic_proposal_editor_cannot_create_or_overwrite_change_order(self):
        with self.assertRaises(ValidationError):save_proposal(self.item.project_id,self.account.pk,change_data(),uuid.uuid4(),0,self.owner)
        child=self.draft()
        with self.assertRaises(ValidationError):save_proposal(child.project_id,child.client_id,proposal_data(),child.pk,1,self.owner,child.pk)
        response=self.client.get(reverse('workqueue:proposal_edit',args=[child.project_id,child.pk]))
        self.assertRedirects(response,reverse('workqueue:change_order_edit',args=[self.base.pk,child.pk]))

    def test_staff_editor_preserves_parent_client_and_has_live_change_summary(self):
        url=reverse('workqueue:change_order_new',args=[self.base.pk])
        response=self.client.get(url)
        self.assertContains(response,'Does this affect the schedule?')
        self.assertContains(response,'data-change-revised')
        self.assertFalse(Proposal.objects.filter(base_revision=self.base).exists())
        response=self.client.post(url,self.post_data(client=str(uuid.uuid4())))
        self.assertEqual(response.status_code,302)
        child=Proposal.objects.get(base_revision=self.base)
        self.assertEqual(child.client_id,self.account.pk)
        self.assertContains(self.client.get(response.url),'Original agreement')
        self.assertContains(self.client.get(reverse('workqueue:proposal_detail',args=[self.base_proposal.pk])),'Add change order')

    def test_bad_schedule_and_stale_posts_return_errors_not_server_errors(self):
        url=reverse('workqueue:change_order_new',args=[self.base.pk])
        for changes in [{'timeline':''},{'proposed_completion_date':'bad-date'},{'additional_days':'-1'},{'scope_html':'x'*40001}]:
            self.assertEqual(self.client.post(url,self.post_data(**changes)).status_code,400)

    def test_staff_and_csrf_guards_apply_and_sent_editor_is_read_only(self):
        url=reverse('workqueue:change_order_new',args=[self.base.pk])
        self.assertEqual(Browser().get(url).status_code,302)
        secure=Browser(enforce_csrf_checks=True);secure.force_login(self.owner)
        self.assertEqual(secure.post(url,self.post_data()).status_code,403)
        child,revision=self.sent()
        self.assertRedirects(self.client.get(reverse('workqueue:change_order_edit',args=[self.base.pk,child.pk])),reverse('workqueue:proposal_detail',args=[child.pk]))

    def test_public_review_requires_matching_invitation_and_explicit_acceptance(self):
        child,revision=self.sent();url=reverse('workqueue:proposal_public',args=[revision.pk])
        guest=Browser();self.assertEqual(guest.get(url).status_code,403)
        session=guest.session;session['proposal_invitation']=self.base.invitation.pk;session.save()
        self.assertEqual(guest.get(url).status_code,403)
        session=guest.session;session['proposal_invitation']=revision.invitation.pk;session.save()
        response=guest.get(url)
        self.assertContains(response,'Accept change order')
        self.assertContains(response,'schedule impact')
        self.assertEqual(guest.post(url,{'action':'accept','full_name':'Example'}).status_code,400)
        self.assertEqual(guest.post(url,{'action':'accept','full_name':'Example','agreement':'on'}).status_code,302)
        revision.refresh_from_db();self.assertEqual(revision.state,'accepted')

    def test_schedule_and_agreement_totals_survive_pdf_and_sent_revisions(self):
        child,revision=self.sent(proposed_completion_date='2026-12-10')
        from .pm_pdf import proposal_pdf
        pdf=PdfReader(BytesIO(proposal_pdf(revision.snapshot,revision)))
        text='\n'.join(page.extract_text() for page in pdf.pages)
        for value in ['Change order','2,000.00','2,250.00','Additional calendar','2026-12-10']:
            self.assertIn(value.lower(),text.lower())
        child.refresh_from_db();revise_proposal(child.pk,child.version,self.owner);child.refresh_from_db()
        self.assertEqual(child.draft['additional_days'],3)
        self.assertEqual(child.draft['proposed_completion_date'],'2026-12-10')

    def test_unaccepted_changes_never_enter_approved_totals_or_mutate_freshbooks(self):
        from .freshbooks_models import FreshBooksInvoice
        child,revision=self.sent()
        respond(revision.invitation.pk,{'action':'changes','full_name':'Example','note':'Please reduce the scope.'})
        self.assertEqual(amounts(self.base)['approved_total'],'2000.00')
        self.assertFalse(FreshBooksInvoice.objects.exists())

    def test_sent_review_rejects_changed_base_or_stale_approved_total(self):
        child,revision=self.sent()
        # Defense against unexpected historical/manual edits outside normal services.
        changed=copy.deepcopy(revision.snapshot);changed['change_order']['approved_changes']='12.00'
        ProposalRevision.objects.filter(pk=revision.pk).update(snapshot=changed)
        with self.assertRaises(ValidationError):respond(revision.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})
        Proposal.objects.filter(pk=self.base_proposal.pk).update(has_draft=True)
        guest=Browser();session=guest.session;session['proposal_invitation']=revision.invitation.pk;session.save()
        self.assertNotContains(guest.get(reverse('workqueue:proposal_public',args=[revision.pk])),'Accept change order')

    def test_portal_financial_access_and_billing_client_boundaries_still_apply(self):
        from .portal_models import PortalMember
        from .portal_services import visible_proposals
        child,revision=self.sent()
        member=PortalMember.objects.create(project=self.item.project,client=self.account,contact=self.contact,email=self.contact.email)
        self.assertFalse(visible_proposals(member).exists())
        member.financial_access=True;member.save()
        self.assertIn(revision,list(visible_proposals(member)))
        other=create_work(data=work_data(contact_email='outsider@example.invalid',company='Other company'),actor=self.owner)[0]
        outsider=PortalMember.objects.create(project=self.item.project,client=other.client_contact.client,contact=other.client_contact,email=other.contact_email,financial_access=True)
        self.assertFalse(visible_proposals(outsider).filter(pk=revision.pk).exists())
        guest=Browser();session=guest.session;session['queue_verified_email']=other.contact_email
        session['queue_email_verified_until']=(timezone.now()+timedelta(days=1)).timestamp();session.save()
        self.assertEqual(guest.get(reverse('workqueue:portal_proposal',args=[self.item.project_id,revision.pk])).status_code,404)


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,INTAKE_OWNER_EMAIL='owner@example.invalid')
class ChangeOrderConcurrency(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_sends_cannot_create_competing_pending_totals(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from .models import QueueState
        QueueState.objects.get_or_create(pk=1)
        owner=get_user_model().objects.create_superuser('change-concurrent',password=None)
        item=create_work(data=work_data(),actor=owner)[0];contact=item.client_contact
        parent=save_proposal(item.project_id,contact.client_id,proposal_data(),uuid.uuid4(),0,owner)
        base=send_proposal(parent.pk,1,contact.pk,uuid.uuid4(),owner)
        respond(base.invitation.pk,{'action':'accept','full_name':'Example','agreement':True})
        drafts=[save_change_order(base.pk,change_data(),uuid.uuid4(),0,owner) for _ in range(2)]
        barrier=Barrier(2)
        def send(pk):
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=owner.pk);barrier.wait(timeout=10)
                try:send_proposal(pk,1,contact.pk,uuid.uuid4(),actor);return 'sent'
                except ValidationError:return 'blocked'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,[d.pk for d in drafts]))
        self.assertCountEqual(results,['sent','blocked'])
        self.assertEqual(ProposalRevision.objects.filter(proposal__base_revision=base,state='sent').count(),1)
