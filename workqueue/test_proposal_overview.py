from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, NotificationDelivery, Status
from .pm_models import Proposal, ProposalRevision, ProposalInvitation, ProjectEvent
from .services import create_work
from .tests import work_data
from .proposal_overview import overview_queryset, overview_row


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class ProposalOverviewTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('overview-owner','owner@example.invalid','test')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.project=self.item.project
        self.client.force_login(self.owner)
        self.url=reverse('workqueue:proposal_overview')
        self.now=timezone.now()
        self.index=0

    def proposal(self, state='sent', delivery='sent', expiry=None, draft=False, **changes):
        self.index+=1
        snapshot={'total':'1250.00','pricing':'fixed','valid_until':'2026-12-01'}
        data=dict(project=self.project,client=self.item.client_contact.client,
            number=f'EST-{self.index:05d}',title=f'Plan {self.index}',draft=snapshot,
            has_draft=draft)
        proposal=Proposal.objects.create(**{**data,**changes})
        if state is not None:
            revision=ProposalRevision.objects.create(proposal=proposal,sequence=1,state=state,
                snapshot=snapshot,recipient='proposal@example.invalid')
            if delivery is not None:
                notice=NotificationDelivery.objects.create(recipient_kind='client',recipient=revision.recipient,
                    state=delivery,body='Private token #DO-NOT-EXPOSE',last_error='private error',
                    sent_at=self.now if delivery=='sent' else None)
                ProposalInvitation.objects.create(revision=revision,digest=f'{self.index:064d}',
                    notification=notice,expires_at=expiry or self.now+timedelta(days=15))
            proposal.current_revision=revision;proposal.save()
        return proposal

    def row(self, proposal):
        return overview_row(overview_queryset(now=self.now).get(pk=proposal.pk))

    def test_current_revision_and_multiple_jobs_do_not_multiply_counts(self):
        proposal=self.proposal()
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        ProposalRevision.objects.create(proposal=proposal,sequence=2,state='changes',snapshot={},recipient='old@example.invalid')
        response=self.client.get(self.url)
        self.assertEqual(response.context['summary']['total'],1)
        self.assertEqual(len(response.context['rows']),1)
        self.assertEqual(response.context['rows'][0]['revision'].sequence,1)
        self.assertEqual(response.context['summary']['awaiting'],1)
        self.assertNotContains(response,'old@example.invalid')

    def test_drafts_take_precedence_over_accepted_and_previous_email_errors(self):
        first=self.proposal(state=None,draft=True)
        second=self.proposal(state='accepted',draft=True,delivery='failed',expiry=self.now-timedelta(days=1))
        self.assertEqual(self.row(first)['label'],'Draft')
        row=self.row(second)
        self.assertEqual(row['label'],'Draft revision')
        self.assertFalse(row['proposal'].email_problem)
        self.assertFalse(row['proposal'].expiring_soon)
        self.assertEqual(self.client.get(self.url,{'state':'attention'}).context['page'].paginator.count,0)

    def test_pending_sending_failure_unknown_canceled_missing_invitation(self):
        for delivery,expected in [('pending','pending'),('sending','pending'),('failed','review'),
                                  ('unknown','review'),('canceled','review'),(None,'review')]:
            row=self.row(self.proposal(delivery=delivery))
            self.assertEqual(row['state'],expected)
            self.assertEqual(row['proposal'].email_problem,expected=='review')
        response=self.client.get(self.url)
        self.assertEqual(response.context['summary']['awaiting'],0)
        self.assertEqual(response.context['summary']['attention'],4)
        self.assertEqual(self.client.get(self.url,{'state':'email'}).context['page'].paginator.count,4)

    def test_exact_expiry_and_seven_day_boundaries(self):
        expired=self.proposal(expiry=self.now)
        valid=self.proposal(expiry=self.now+timedelta(seconds=1))
        edge=self.proposal(expiry=self.now+timedelta(days=7))
        later=self.proposal(expiry=self.now+timedelta(days=7,seconds=1))
        self.assertEqual(self.row(expired)['state'],'expired')
        self.assertTrue(self.row(valid)['proposal'].expiring_soon)
        self.assertTrue(self.row(edge)['proposal'].expiring_soon)
        self.assertFalse(self.row(later)['proposal'].expiring_soon)
        self.assertEqual(overview_queryset(now=self.now).filter(expiring_soon=True).count(),2)

    def test_accepted_changes_withdrawn_are_not_reclassified_by_expiration(self):
        for state in ['accepted','changes','withdrawn']:
            row=self.row(self.proposal(state=state,expiry=self.now-timedelta(days=1)))
            self.assertEqual(row['state'],state)
            self.assertFalse(row['proposal'].expiring_soon)
        self.assertEqual(self.client.get(self.url,{'state':'attention'}).context['page'].paginator.count,1)

    def test_viewed_and_sent_share_awaiting_bucket_without_claiming_delivery(self):
        sent=self.proposal()
        viewed=self.proposal(state='viewed')
        ProposalRevision.objects.filter(pk=viewed.current_revision_id).update(viewed_at=self.now)
        self.assertEqual(self.row(sent)['state'],'awaiting')
        self.assertEqual(self.row(viewed)['state'],'awaiting')
        response=self.client.get(self.url)
        self.assertContains(response,'First opened')
        self.assertContains(response,'does not confirm inbox delivery')
        self.assertContains(response,'automated email security checks')

    def test_active_scope_excludes_closed_deleted_jobs_but_all_preserves_history(self):
        self.proposal()
        WorkItem.objects.filter(pk=self.item.pk).update(status=Status.COMPLETED)
        self.assertFalse(overview_queryset(scope='active').exists())
        sibling=create_work(data=work_data(kind='update',project=self.project),actor=self.owner)[0]
        self.assertEqual(overview_queryset(scope='active').count(),1)
        WorkItem.all_objects.filter(pk=sibling.pk).update(deleted_at=self.now)
        self.assertFalse(overview_queryset(scope='active').exists())
        WorkItem.all_objects.filter(pk=self.item.pk).update(deleted_at=self.now)
        self.assertEqual(overview_queryset().count(),1)

    def test_search_matches_references_recipients_and_project_client_without_duplicates(self):
        proposal=self.proposal()
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        for query in [proposal.number,proposal.title,self.project.name,self.project.street,
                      self.item.reference,self.item.contact_email,'proposal@example.invalid',proposal.client.name]:
            self.assertEqual(overview_queryset(query).count(),1,query)
        self.assertFalse(overview_queryset('nonexistent').exists())

    def test_status_filter_leaves_search_scope_counts_intact_and_rejects_bad_values(self):
        self.proposal();self.proposal(state='changes');self.proposal(state=None,draft=True)
        response=self.client.get(self.url,{'state':'changes'})
        self.assertEqual(response.context['summary']['total'],3)
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertEqual(self.client.get(self.url,{'state':'invalid','scope':'invalid'}).context['state'],'all')
        self.assertEqual(len(self.client.get(self.url,{'q':'x'*250}).context['q']),200)

    def test_fees_use_current_draft_and_invalid_legacy_values_do_not_crash(self):
        proposal=self.proposal(draft=True)
        proposal.draft={'total':'12.345','pricing':'hourly'};proposal.save()
        row=self.row(proposal)
        self.assertTrue(row['hourly']);self.assertEqual(row['amount'],Decimal('12.35'))
        for bad in [[],{'total':'NaN'},{'total':'Infinity'},{'total':'1e999999'}, {'total':'-1'}, {'total':'broken'}]:
            proposal.draft=bad;proposal.save()
            self.assertIsNone(self.row(proposal)['amount'])
        annotated=overview_queryset().get(pk=proposal.pk)
        annotated.draft=None
        self.assertIsNone(overview_row(annotated)['amount'])

    def test_missing_revision_is_reviewable_and_markup_and_secrets_are_private(self):
        proposal=self.proposal(state=None,draft=False,title='<script>alert(1)</script>')
        self.assertEqual(self.row(proposal)['state'],'review')
        self.proposal();self.proposal(delivery=None)
        response=self.client.get(self.url)
        self.assertContains(response,'&lt;script&gt;')
        for secret in ['DO-NOT-EXPOSE','private error','/proposals/access/#']:
            self.assertNotContains(response,secret)
        self.assertContains(response,'Email record unavailable')

    def test_rows_load_in_one_query_without_per_proposal_lookups(self):
        self.proposal();self.proposal(delivery=None);self.proposal(state=None,draft=True)
        with self.assertNumQueries(1):
            rows=[overview_row(p) for p in overview_queryset()]
        self.assertEqual(len(rows),3)

    def test_pagination_stable_and_reads_do_not_mutate_or_queue_messages(self):
        for index in range(31):self.proposal(state=None,draft=True)
        before=(Proposal.objects.count(),ProposalRevision.objects.count(),NotificationDelivery.objects.count(),ProjectEvent.objects.count())
        one=self.client.get(self.url);two=self.client.get(self.url,{'page':2})
        self.assertEqual(len(one.context['rows']),30);self.assertEqual(len(two.context['rows']),1)
        self.assertEqual(one.context['summary']['total'],31)
        ids1={r['proposal'].pk for r in one.context['rows']};ids2={r['proposal'].pk for r in two.context['rows']}
        self.assertFalse(ids1 & ids2)
        self.assertEqual(self.client.post(self.url,{'state':'accepted'}).status_code,405)
        self.assertEqual(before,(Proposal.objects.count(),ProposalRevision.objects.count(),NotificationDelivery.objects.count(),ProjectEvent.objects.count()))

    def test_private_staff_readonly_preview_and_feature_switch(self):
        self.proposal()
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)
        viewer=get_user_model().objects.create_user('proposal-viewer',is_staff=True)
        self.client.force_login(viewer);self.assertEqual(self.client.get(self.url).status_code,403)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        response=self.client.get(self.url);self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        with override_settings(WORK_QUEUE_ENABLED=False):self.assertEqual(self.client.get(self.url).status_code,404)
        with override_settings(WORK_QUEUE_ENABLED=False,WORK_QUEUE_STAFF_PREVIEW=True):
            self.assertEqual(self.client.get(self.url).status_code,200)
