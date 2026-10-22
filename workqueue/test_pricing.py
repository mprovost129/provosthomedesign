import copy,uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection
from django.test import TestCase,override_settings,Client as Browser
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Client,WorkItem,NotificationDelivery,ClientEvent
from .pricing import (CONTRACTOR_RATES,calculate,save_estimate,save_rate_card,create_proposal)
from .pricing_models import PricingEstimate,PricingEstimateRevision,PricingRateVersion
from .pricing_forms import PricingForm
from .crm import edit_client,create_client
from .crm_forms import NewClientForm
from .pm_models import Proposal
from .services import create_work,EditConflict
from .tests import work_data
from .test_crm_manual import client_data


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True)
class PricingTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('pricing-owner','owner@example.invalid','test')
        self.client.force_login(self.owner)
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.account=self.item.client_contact.client
        self.account.pricing_category='contractor';self.account.save()
        self.card=PricingRateVersion.objects.filter(category='contractor').first()
        if not self.card:
            self.card=PricingRateVersion.objects.create(category='contractor',sequence=1,label='Contractor pricing',rates=CONTRACTOR_RATES)
        self.data={'title':'Addition pricing','client':self.account,'project':self.item.project,'category':'contractor',
            'rate_version':self.card,'mode':'area','project_review':'standard','new_area':'2000','attached_area':'500',
            'override_total':None,'internal_notes':'PRIVATE-COST-NOTES','rate_reason':'','override_reason':''}

    def save(self,**changes):
        return save_estimate(data={**self.data,**changes},token=uuid.uuid4(),version=0,actor=self.owner)

    def proposal_data(self,**changes):
        return {'title':'Design services','scope':'Draw the existing structure and proposed changes',
            'deliverables':'Existing-condition drawings and renovation design drawings','valid_until':timezone.localdate()+timedelta(days=30),
            'review_confirmed':True,'review_note':'',**changes}

    def post_data(self,**changes):
        data={**self.data,'token':str(uuid.uuid4()),'version':'0','client':str(self.account.pk),
            'project':str(self.item.project_id),'rate_version':str(self.card.pk),'action':'save'}
        return {key:value for key,value in {**data,**changes}.items() if value is not None}

    def test_approved_guide_examples_and_independent_existing_renovation_areas(self):
        self.assertEqual(calculate(self.data,self.card)['total'],'2875.00')
        values={**self.data,'new_area':0,'attached_area':0,'existing_area':'2000','renovation_area':'600'}
        result=calculate(values,self.card)
        self.assertEqual(result['total'],'1950.00')
        self.assertEqual([line['amount'] for line in result['lines']],['1500.00','450.00'])
        self.assertEqual(calculate({**values,'existing_area':0,'renovation_area':0,'detached_area':600,'detached_above_area':400},self.card)['total'],'1250.00')

    def test_decimal_rounding_and_hourly_alternative(self):
        values={**self.data,'new_area':0,'attached_area':0,'existing_area':'.02','renovation_area':'.02'}
        self.assertEqual(calculate(values,self.card)['total'],'0.04')
        result=calculate({**self.data,'mode':'hourly','hours':'12.5','custom_fee':50,'override_total':1},self.card)
        self.assertEqual(result['total'],'1562.50');self.assertEqual(result['pricing'],'hourly')
        self.assertEqual(len(result['lines']),1)

    def test_missing_or_invalid_quantities_and_unexplained_price_changes_are_rejected(self):
        for changes in [{'new_area':0,'attached_area':0},{'new_area':'NaN'},{'new_area':'-1'},
            {'new_area':'1.001'},{'new_rate':'.25'},{'override_total':'100'},
            {'custom_fee':'10','custom_description':''}]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):calculate({**self.data,**changes},self.card)
        result=calculate({**self.data,'override_total':0,'override_reason':'Approved no-charge test'},self.card)
        self.assertEqual(result['total'],'0.00');self.assertTrue(result['overridden'])

    def test_saved_history_and_proposal_prices_do_not_follow_rate_changes(self):
        saved=self.save();revision=saved.current_revision
        original=copy.deepcopy(revision.snapshot)
        save_rate_card(category='contractor',label='Updated contractor',rates={**CONTRACTOR_RATES,'new':'2'},source_note='Test',expected=self.card.sequence,actor=self.owner)
        revision.refresh_from_db();self.assertEqual(revision.snapshot,original)
        proposal=create_proposal(estimate_id=saved.pk,version=saved.version,data=self.proposal_data(),actor=self.owner)
        self.assertEqual(proposal.draft['total'],'2875.00')
        self.assertNotIn('PRIVATE-COST-NOTES',str(proposal.draft))
        saved=save_estimate(data={**self.data,'new_area':'1000'},token=saved.pk,version=saved.version,actor=self.owner,estimate_id=saved.pk)
        self.assertEqual(saved.version,2);self.assertEqual(saved.revisions.count(),2)
        proposal.refresh_from_db();self.assertEqual(proposal.draft['total'],'2875.00')

    def test_quick_estimates_and_idempotent_saves_and_proposal_creation(self):
        token=uuid.uuid4()
        first=save_estimate(data=self.data,token=token,version=0,actor=self.owner)
        again=save_estimate(data=self.data,token=token,version=0,actor=self.owner)
        self.assertEqual(first.pk,again.pk);self.assertEqual(PricingEstimateRevision.objects.count(),1)
        proposal=create_proposal(estimate_id=first.pk,version=first.version,data=self.proposal_data(),actor=self.owner)
        retry=create_proposal(estimate_id=first.pk,version=first.version,data=self.proposal_data(),actor=self.owner)
        self.assertEqual(proposal.pk,retry.pk);self.assertEqual(Proposal.objects.count(),1)
        self.assertFalse(NotificationDelivery.objects.exists())
        quick=self.save(client=None,project=None)
        with self.assertRaises(ValidationError):create_proposal(estimate_id=quick.pk,version=quick.version,data=self.proposal_data(),actor=self.owner)

    def test_category_matching_no_fallback_and_change_does_not_reprice_history(self):
        self.assertFalse(PricingRateVersion.objects.filter(category='homeowner').exists())
        saved=self.save()
        self.account.pricing_category='homeowner';self.account.save()
        with self.assertRaises(ValidationError):self.save()
        with self.assertRaises(ValidationError):create_proposal(estimate_id=saved.pk,version=saved.version,data=self.proposal_data(),actor=self.owner)
        saved.current_revision.refresh_from_db();self.assertEqual(saved.current_revision.snapshot['result']['total'],'2875.00')
        form=PricingForm(self.post_data(category='homeowner',rate_version=''))
        self.assertFalse(form.is_valid());self.assertIn('rate_version',form.errors)
        self.account.pricing_category='';self.account.save()
        with self.assertRaises(ValidationError):self.save()

    def test_custom_rates_overrides_and_individual_project_review_stay_private(self):
        saved=self.save(new_rate='1.50',rate_reason='PRIVATE-RATE-REASON',override_total='2500',
            override_reason='PRIVATE-OVERRIDE',project_review='custom')
        with self.assertRaises(ValidationError):create_proposal(estimate_id=saved.pk,version=saved.version,data=self.proposal_data(),actor=self.owner)
        proposal=create_proposal(estimate_id=saved.pk,version=saved.version,data=self.proposal_data(review_note='Reviewed multi-unit circumstances'),actor=self.owner)
        self.assertEqual(proposal.draft['total'],'2500.00');self.assertEqual(len(proposal.draft['lines']),1)
        for private in ['PRIVATE-RATE-REASON','PRIVATE-OVERRIDE','PRIVATE-COST-NOTES','Reviewed multi-unit circumstances']:
            self.assertNotIn(private,str(proposal.draft))

    def test_stale_edits_mismatched_projects_and_unreviewed_proposals_are_rejected(self):
        saved=self.save()
        with self.assertRaises(EditConflict):save_estimate(data=self.data,token=saved.pk,version=0,actor=self.owner,estimate_id=saved.pk)
        other=create_work(data=work_data(contact_email='other@example.invalid',contact_full_name='Other'),actor=self.owner)[0]
        with self.assertRaises(ValidationError):self.save(project=other.project)
        with self.assertRaises(ValidationError):create_proposal(estimate_id=saved.pk,version=saved.version,data=self.proposal_data(review_confirmed=False),actor=self.owner)

    def test_staff_permissions_csrf_and_read_only_pages(self):
        saved=self.save()
        urls=[reverse('workqueue:pricing_estimates'),reverse('workqueue:pricing_new'),reverse('workqueue:pricing_detail',args=[saved.pk])]
        with CaptureQueriesContext(connection) as queries:
            for url in urls:self.assertEqual(self.client.get(url).status_code,200)
        self.assertFalse(any(item['sql'].lstrip().split()[0] in {'INSERT','UPDATE','DELETE'} for item in queries))
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.owner)
        self.assertEqual(browser.post(reverse('workqueue:pricing_new'),self.post_data()).status_code,403)
        self.client.logout()
        for url in urls:self.assertIn('/staff/login/',self.client.get(url).url)
        staff=get_user_model().objects.create_user('price-reader',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'));self.client.force_login(staff)
        self.assertEqual(self.client.get(urls[0]).status_code,200)
        self.assertEqual(self.client.get(urls[1]).status_code,403)
        staff.user_permissions.add(Permission.objects.get(codename='change_workitem'))
        self.assertEqual(self.client.get(reverse('workqueue:pricing_rates')).status_code,403)

    def test_page_save_detail_history_and_proposal_draft_links(self):
        response=self.client.post(reverse('workqueue:pricing_new'),self.post_data())
        self.assertEqual(response.status_code,302)
        estimate=PricingEstimate.objects.get()
        response=self.client.get(response.url)
        self.assertContains(response,'2875.00');self.assertContains(response,'Create a proposal draft')
        data=self.proposal_data();data['version']=estimate.version;data['valid_until']=data['valid_until'].isoformat()
        response=self.client.post(reverse('workqueue:pricing_detail',args=[estimate.pk]),data)
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(reverse('workqueue:client_detail',args=[self.account.pk])),estimate.number)
        self.assertContains(self.client.get(reverse('workqueue:project_detail',args=[self.item.project_id])),estimate.number)

    def test_client_dropdown_creation_edit_audit_and_old_forms_preserve_classification(self):
        values=client_data(name='Builder',kind='company',pricing_category='contractor')
        account,_=create_client(data=values,nonce=uuid.uuid4(),actor=self.owner)
        self.assertEqual(account.pricing_category,'contractor')
        edit_client(client_id=account.pk,version=account.version,data={'pricing_category':'other'},actor=self.owner)
        account.refresh_from_db()
        self.assertTrue(ClientEvent.objects.filter(client=account,changes__pricing_category__after='other').exists())
        values['intake_token']='test-token';self.assertTrue(NewClientForm(values).is_valid())
        values.pop('pricing_category');self.assertFalse(NewClientForm(values).is_valid())
        from .crm_forms import ClientForm
        values={'client-version':account.version,'client-name':account.name,'client-kind':account.kind}
        form=ClientForm(values,instance=account,prefix='client')
        self.assertTrue(form.is_valid());self.assertEqual(form.cleaned_data['pricing_category'],'other')

    def test_invalid_revision_query_is_a_not_found_response(self):
        saved=self.save()
        self.assertEqual(self.client.get(reverse('workqueue:pricing_detail',args=[saved.pk]),{'revision':'bad'}).status_code,404)

    def test_each_configured_client_category_uses_its_own_default_rates(self):
        for category,rate in [('homeowner','2.00'),('other','3.00')]:
            card=save_rate_card(category=category,label=category.title()+' test rates',
                rates={**CONTRACTOR_RATES,'new':rate},source_note='Fictional test only',expected=0,actor=self.owner)
            self.account.pricing_category=category;self.account.save()
            response=self.client.get(reverse('workqueue:pricing_new'),{'client':str(self.account.pk)})
            self.assertEqual(response.context['form'].initial['category'],category)
            self.assertEqual(response.context['form'].initial['rate_version'],card.pk)
            self.assertEqual(response.context['form'].initial['new_rate'],rate)
            form=PricingForm(self.post_data(category=category,rate_version=str(card.pk),new_area='1000',attached_area='0'))
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.result['total'],str(Decimal(rate)*1000))
