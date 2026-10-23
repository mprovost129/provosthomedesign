import copy
import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client as Browser, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .models import Client, InvoiceDocument, InvoiceDocumentRevision, NotificationDelivery, WorkItem
from .freshbooks_models import FreshBooksInvoice
from .document_content import safe_rich, checked_rich
from .document_services import save_invoice_document
from .pm_forms import ProposalForm, LineFormSet, draft_data
from .pm_services import save_proposal, send_proposal, revise_proposal, snapshot_for
from .pm_pdf import proposal_pdf
from .services import create_work, EditConflict
from .tests import work_data
from .test_pm import proposal_data


def document_data(**changes):
    data=proposal_data(document_kind='proposal',document_style='phd-studio-v1',issue_date=timezone.localdate().isoformat(),
        document_reference='Client reference 26-12',notes='Client-facing note',rich_sections={})
    data.update(changes);return data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,
    INTAKE_PUBLIC_BASE_URL='https://testserver',CONTACT_ADDRESS='7 Example Office Street',CONTACT_EMAIL='office@example.invalid')
class DocumentEditorTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_superuser('document-owner','owner@example.invalid','test-only')
        self.item=create_work(data=work_data(),actor=self.actor)[0]
        self.project=self.item.project;self.contact=self.item.client_contact;self.account=self.contact.client
        self.client.force_login(self.actor)

    def invoice_data(self,**changes):
        return document_data(document_kind='invoice',scope='',deliverables='',due_date=(timezone.localdate()+timedelta(days=30)).isoformat(),**changes)

    def invoice(self,**changes):
        return save_invoice_document(project_id=self.project.pk,client_id=self.account.pk,data=self.invoice_data(**changes),token=uuid.uuid4(),version=0,actor=self.actor)

    def post_data(self,kind='proposal',**changes):
        data=document_data(document_kind=kind)
        data.update(client=str(self.account.pk),token=str(uuid.uuid4()),version='0',
            due_date=(timezone.localdate()+timedelta(days=30)).isoformat(),**{'lines-TOTAL_FORMS':'2','lines-INITIAL_FORMS':'0',
            'lines-0-description':'Existing-condition drawings','lines-0-quantity':'1250','lines-0-rate':'0.75','lines-0-ORDER':'1',
            'lines-1-description':'Renovation design','lines-1-quantity':'600','lines-1-rate':'0.75','lines-1-ORDER':'0'})
        data.update(changes);return data

    def test_new_routes_require_staff_and_view_or_edit_permissions(self):
        urls=[reverse('workqueue:documents'),reverse('workqueue:invoice_document_new',args=[self.project.pk]),reverse('workqueue:proposal_new',args=[self.project.pk])]
        anonymous=Browser()
        for url in urls:self.assertEqual(anonymous.get(url).status_code,302)
        limited=get_user_model().objects.create_user('document-viewer',is_staff=True)
        self.client.force_login(limited)
        for url in urls:self.assertEqual(self.client.get(url).status_code,403)
        limited.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(limited)
        self.assertEqual(self.client.get(urls[0]).status_code,200)
        self.assertEqual(self.client.get(urls[1]).status_code,403)

    def test_start_loads_only_related_projects_and_get_never_creates_documents(self):
        other=create_work(data=work_data(contact_email='another@example.invalid',company='Different Company',project_name='Other private project'),actor=self.actor)[0]
        response=self.client.get(reverse('workqueue:documents'),{'client':self.account.pk,'action':'load'})
        self.assertContains(response,self.project.name)
        self.assertNotContains(response,'Other private project')
        self.assertFalse(InvoiceDocument.objects.exists())
        response=self.client.get(reverse('workqueue:documents'),{'client':self.account.pk,'project':self.project.pk,'kind':'invoice','action':'start'})
        self.assertEqual(response.status_code,302);self.assertIn('invoice-document',response.url)
        self.assertFalse(InvoiceDocument.objects.exists())

    def test_estimate_can_be_short_but_proposal_requires_scope_and_deliverables(self):
        for kind,expected in [('estimate',True),('proposal',False)]:
            form=ProposalForm(self.post_data(kind,scope='',deliverables=''),project=self.project)
            self.assertEqual(form.is_valid(),expected,form.errors)
        response=self.client.get(reverse('workqueue:proposal_new',args=[self.project.pk]),{'kind':'estimate'})
        self.assertContains(response,'data-document-editor')
        self.assertEqual(response.context['form'].initial['document_kind'],'estimate')

    def test_saved_proposal_uses_ordered_services_recomputed_total_and_new_style(self):
        response=self.client.post(reverse('workqueue:proposal_new',args=[self.project.pk]),self.post_data('estimate',scope='',deliverables=''))
        self.assertEqual(response.status_code,302)
        from .pm_models import Proposal
        proposal=Proposal.objects.get()
        self.assertEqual(proposal.document_label,'Estimate')
        self.assertEqual(proposal.draft['total'],'1387.50')
        self.assertEqual(proposal.draft['lines'][0]['description'],'Renovation design')
        self.assertEqual(proposal.draft['document_style'],'phd-studio-v1')
        self.assertFalse(NotificationDelivery.objects.exists())
        response=self.client.get(response.url)
        self.assertContains(response,'doc-saved-paper')

    def test_invoice_post_saves_working_paper_and_never_issues_or_sends(self):
        before=list(WorkItem.objects.values_list('id','version','status','queue_order'))
        response=self.client.post(reverse('workqueue:invoice_document_new',args=[self.project.pk]),self.post_data('invoice'))
        self.assertEqual(response.status_code,302,response.content[:1000])
        document=InvoiceDocument.objects.get()
        self.assertEqual(document.draft['total'],'1387.50')
        self.assertEqual(document.revisions.count(),1)
        self.assertEqual(document.client_id,self.account.pk)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertFalse(FreshBooksInvoice.objects.exists())
        self.assertEqual(before,list(WorkItem.objects.values_list('id','version','status','queue_order')))
        self.assertContains(self.client.get(response.url),'not an issued invoice')

    def test_invoice_idempotency_conflict_and_immutable_saved_versions(self):
        token=uuid.uuid4();args=dict(project_id=self.project.pk,client_id=self.account.pk,data=self.invoice_data(),token=token,version=0,actor=self.actor)
        first=save_invoice_document(**args)
        self.assertEqual(save_invoice_document(**args).pk,first.pk)
        self.assertEqual(first.revisions.count(),1)
        with self.assertRaises(EditConflict):save_invoice_document(**args,document_id=first.pk)
        old=copy.deepcopy(first.revisions.get().snapshot)
        self.account.billing_street='Updated billing street';self.account.save()
        second=save_invoice_document(**{**args,'data':self.invoice_data(title='Updated working paper'),'version':1},document_id=first.pk)
        self.assertEqual(second.version,2)
        self.assertEqual(second.revisions.get(sequence=1).snapshot,old)
        self.assertEqual(second.revisions.get(sequence=2).snapshot['billing_address'].split('\n')[0],'Updated billing street')

    def test_invoice_cannot_attach_to_an_unrelated_client_or_be_saved_without_permission(self):
        other=create_work(data=work_data(contact_email='unrelated@example.invalid',company='Other company'),actor=self.actor)[0].client_contact.client
        with self.assertRaises(ValidationError):
            save_invoice_document(project_id=self.project.pk,client_id=other.pk,data=self.invoice_data(),token=uuid.uuid4(),version=0,actor=self.actor)
        with self.assertRaises(PermissionDenied):
            save_invoice_document(project_id=self.project.pk,client_id=self.account.pk,data=self.invoice_data(),token=uuid.uuid4(),version=0,actor=None)
        self.assertFalse(InvoiceDocument.objects.exists())

    def test_invoice_dates_invalid_clients_and_large_rich_sections_show_errors_without_500(self):
        url=reverse('workqueue:invoice_document_new',args=[self.project.pk])
        for changes in [{'due_date':'2020-01-01'},{'client':'not-a-uuid'},{'terms_html':'x'*40001}]:
            response=self.client.post(url,self.post_data('invoice',**changes))
            self.assertEqual(response.status_code,400)
        self.assertFalse(InvoiceDocument.objects.exists())

    def test_browser_and_csrf_guards_apply_to_invoice_editor(self):
        secure=Browser(enforce_csrf_checks=True);secure.force_login(self.actor)
        url=reverse('workqueue:invoice_document_new',args=[self.project.pk])
        self.assertEqual(secure.post(url,self.post_data('invoice')).status_code,403)

    def test_invoice_pdf_draft_warning_and_historical_revision_are_private(self):
        document=self.invoice()
        url=reverse('workqueue:invoice_document_pdf',args=[document.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertIn('NOT A REQUEST FOR PAYMENT',text)
        self.assertIn('156.88',text)
        self.assertIn('7 Example Office Street',text)
        self.assertNotIn('internal_notes',text)
        self.assertEqual(Browser().get(url).status_code,302)
        self.assertEqual(self.client.get(url,{'revision':'999'}).status_code,404)

    def test_rich_text_is_allowlisted_and_stale_html_cannot_hide_different_terms(self):
        html,text=safe_rich('<p onclick="bad()"><strong>Scope</strong><script>alert(1)</script><img src="https://bad.invalid"><a href="javascript:bad()"> here</a></p>')
        self.assertIn('<strong>Scope</strong>',html)
        for unsafe in ['script','onclick','img','href','bad.invalid']:self.assertNotIn(unsafe,html)
        self.assertEqual(text,'Scope here')
        self.assertEqual(checked_rich({'terms':'Different terms','rich_sections':{'terms':'<b>Old terms</b>'}}),{})

    def test_sent_versions_preserve_kind_rich_content_and_appearance_when_revising(self):
        proposal=save_proposal(self.project.pk,self.account.pk,document_data(document_kind='estimate',terms='Pay after approval',
            rich_sections={'terms':'<p><strong>Pay</strong> after approval</p>'}),uuid.uuid4(),0,self.actor)
        revision=send_proposal(proposal.pk,1,self.contact.pk,uuid.uuid4(),self.actor)
        frozen=copy.deepcopy(revision.snapshot)
        self.assertIn('<strong>',frozen['rich_sections']['terms'])
        proposal.refresh_from_db();revise_proposal(proposal.pk,proposal.version,self.actor);proposal.refresh_from_db()
        self.assertEqual(proposal.draft['document_kind'],'estimate')
        self.assertEqual(proposal.draft['rich_sections'],frozen['rich_sections'])
        revision.refresh_from_db();self.assertEqual(revision.snapshot,frozen)

    def test_legacy_pdf_renderer_is_preserved_and_new_pdf_is_supported(self):
        from .pm_services import checked_draft
        legacy=dict(checked_draft(proposal_data()),number='EST-00001',client_name='Example',billing_address='',project_name='House',project_address='')
        with patch('workqueue.document_pdf.document_pdf',return_value=b'new-pdf') as renderer:
            self.assertTrue(proposal_pdf(legacy).startswith(b'%PDF'))
            renderer.assert_not_called()
            self.assertEqual(proposal_pdf({**legacy,'document_style':'phd-studio-v1'}),b'new-pdf')

    def test_estimate_public_view_uses_estimate_wording_and_sanitized_rich_content(self):
        proposal=save_proposal(self.project.pk,self.account.pk,document_data(document_kind='estimate'),uuid.uuid4(),0,self.actor)
        revision=send_proposal(proposal.pk,1,self.contact.pk,uuid.uuid4(),self.actor)
        browser=Browser();session=browser.session;session['proposal_invitation']=revision.invitation.pk;session.save()
        response=browser.get(reverse('workqueue:proposal_public',args=[revision.pk]))
        self.assertContains(response,'Your project estimate')
        self.assertContains(response,'Accept estimate')
        self.assertContains(response,'this estimate’s pricing and terms')

    def test_new_pdf_rich_text_and_multi_page_services_render(self):
        from .document_pdf import document_pdf
        snapshot=dict(document_data(terms='Pay after approval',rich_sections={'terms':'<p><b>Pay</b> after approval</p>'}),
            number='EST-TEST',client_name='Example client',billing_address='1 Example Road',project_name='Example project',project_address='2 Project Lane')
        snapshot['lines']=[{'description':f'Service {n}: '+'Design and coordination. '*15,'quantity':'1','rate':'10','amount':'10'} for n in range(20)]
        snapshot['total']='200.00'
        pdf=PdfReader(BytesIO(document_pdf(snapshot)))
        self.assertGreater(len(pdf.pages),1)
        text='\n'.join(page.extract_text() for page in pdf.pages)
        self.assertIn('Service 19',text);self.assertIn('200.00',text)

    def test_numbered_terms_keep_their_numbering_in_pdf(self):
        from .document_pdf import document_pdf
        snapshot=dict(document_data(terms='First condition\nSecond condition',
            rich_sections={'terms':'<ol><li>First condition</li><li>Second condition</li></ol>'}),
            number='EST-LIST',client_name='Example',billing_address='',project_name='House',project_address='',total='10',
            lines=[{'description':'Design','quantity':'1','rate':'10','amount':'10'}])
        text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(document_pdf(snapshot))).pages)
        self.assertIn('1. First condition',text);self.assertIn('2. Second condition',text)

    def test_accepted_document_prefills_invoice_without_creating_or_changing_records(self):
        from .pm_services import respond
        proposal=save_proposal(self.project.pk,self.account.pk,document_data(),uuid.uuid4(),0,self.actor)
        revision=send_proposal(proposal.pk,1,self.contact.pk,uuid.uuid4(),self.actor)
        respond(revision.invitation.pk,dict(action='accept',full_name='Example Client',agreement=True))
        revision.refresh_from_db();frozen=copy.deepcopy(revision.snapshot)
        notifications=NotificationDelivery.objects.count()
        detail=self.client.get(reverse('workqueue:proposal_detail',args=[proposal.pk]))
        self.assertContains(detail,'Prepare invoice working draft')
        response=self.client.get(reverse('workqueue:invoice_document_new',args=[self.project.pk]),{'proposal':proposal.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form'].initial['lines'],frozen['lines'])
        self.assertEqual(response.context['form'].initial['client'],self.account)
        self.assertFalse(InvoiceDocument.objects.exists())
        self.assertEqual(NotificationDelivery.objects.count(),notifications)
        revision.refresh_from_db();self.assertEqual(revision.snapshot,frozen)

    def test_invoice_can_be_edited_after_a_client_merge_without_rewriting_old_versions(self):
        from .crm import merge_clients
        document=self.invoice();frozen=copy.deepcopy(document.revisions.get().snapshot)
        target=create_work(data=work_data(contact_email='kept@example.invalid',company='Kept client'),actor=self.actor)[0].client_contact.client
        self.account.refresh_from_db();target.refresh_from_db()
        merge_clients(source_id=self.account.pk,source_version=self.account.version,target_id=target.pk,target_version=target.version,actor=self.actor)
        response=self.client.get(reverse('workqueue:invoice_document_edit',args=[self.project.pk,document.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form'].initial['client'],target)
        changed=save_invoice_document(project_id=self.project.pk,client_id=target.pk,data=self.invoice_data(),token=document.pk,version=1,actor=self.actor,document_id=document.pk)
        self.assertEqual(changed.client_id,target.pk)
        self.assertEqual(changed.revisions.get(sequence=1).snapshot,frozen)
