import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, Client as Browser, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .models import QueueState, NotificationDelivery
from .services import create_work, EditConflict
from .tests import work_data
from .test_documents import document_data
from .test_pm import proposal_data
from .document_services import save_invoice_document
from .document_models import InvoiceDocument
from .native_invoice_models import NativeBillingPlan, NativeInvoice, NativeInvoiceEntry, NativeInvoiceEvent
from .native_invoices import create_plan, close_plan, issue_test_invoice, record_entry, reverse_entry, void_invoice, balance, plan_summary
from .pm_services import save_proposal, send_proposal, respond
from .freshbooks_models import FreshBooksInvoice


class InvoiceFixtures:
    def seed_billing(self):
        QueueState.objects.get_or_create(pk=1)
        self.actor=get_user_model().objects.create_superuser('billing-owner','owner@example.invalid','test-only')
        self.item=create_work(data=work_data(),actor=self.actor)[0]
        self.project=self.item.project;self.account=self.item.client_contact.client
        self.document=self.draft()

    def draft(self,**changes):
        data=document_data(document_kind='invoice',scope='',deliverables='',
            due_date=(timezone.localdate()+timedelta(days=30)).isoformat(),
            lines=[{'description':'Design services','quantity':'1','rate':'1000'}])
        data.update(changes)
        return save_invoice_document(project_id=self.project.pk,client_id=self.account.pk,data=data,token=uuid.uuid4(),version=0,actor=self.actor)

    def plan(self,**changes):
        args=dict(document_id=self.document.pk,version=1,discount='0',tax_percent='0',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return create_plan(**args)

    def invoice(self,plan,**changes):
        args=dict(plan_id=plan.pk,stage='progress',amount='100',description='Work completed',issue_date=timezone.localdate(),
            due_date=timezone.localdate()+timedelta(days=30),token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return issue_test_invoice(**args)

    def entry(self,invoice,**changes):
        invoice.refresh_from_db()
        args=dict(invoice_id=invoice.pk,version=invoice.version,kind='payment',amount='25',entry_date=timezone.localdate(),
            method='check',reference='TEST-CHECK-123',note='Internal accounting note',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return record_entry(**args)

    def reverse(self,invoice,entry,**changes):
        invoice.refresh_from_db()
        args=dict(invoice_id=invoice.pk,entry_id=entry.pk,version=invoice.version,note='Wrong entry; correcting the record',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return reverse_entry(**args)

    def void(self,invoice,**changes):
        invoice.refresh_from_db()
        args=dict(invoice_id=invoice.pk,version=invoice.version,note='Replace this test invoice',token=uuid.uuid4(),actor=self.actor)
        args.update(changes);return void_invoice(**args)

    def accepted(self):
        proposal=save_proposal(self.project.pk,self.account.pk,proposal_data(lines=[{'description':'Accepted work','quantity':'1','rate':'1000'}]),uuid.uuid4(),0,self.actor)
        result=send_proposal(proposal.pk,1,self.item.client_contact.pk,uuid.uuid4(),self.actor)
        respond(result.invitation.pk,{'action':'accept','full_name':'Test signer','agreement':True})
        return result


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False,INTAKE_PUBLIC_BASE_URL='https://testserver')
class NativeInvoiceTests(InvoiceFixtures,TestCase):
    def setUp(self):
        self.seed_billing();self.client.force_login(self.actor)

    def test_frozen_plan_discount_tax_and_saved_source(self):
        plan=self.plan(discount='100',tax_percent='6.25')
        self.assertEqual(plan.total,Decimal('956.25'))
        self.assertEqual(plan.snapshot['tax'],'56.25')
        old=copy.deepcopy(plan.snapshot)
        self.document.draft['title']='Later draft';self.document.save()
        self.account.name='Renamed client';self.account.save()
        plan.refresh_from_db();self.assertEqual(plan.snapshot,old)
        self.assertEqual(plan.revision.sequence,1)

    def test_plan_retry_conflict_duplicate_and_invalid_money(self):
        token=uuid.uuid4();plan=self.plan(token=token)
        self.assertEqual(self.plan(token=token).pk,plan.pk)
        with self.assertRaises(ValidationError):self.plan(token=token,discount='1')
        with self.assertRaises(ValidationError):self.plan()
        other=self.draft()
        with self.assertRaises(EditConflict):self.plan(document_id=other.pk,version=2)
        for field,value in [('discount','1000'),('discount','-1'),('discount','0.001'),('discount','NaN'),('tax_percent','101'),('tax_percent','Infinity')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValidationError):
                self.plan(document_id=other.pk,**{field:value})

    def test_one_plan_per_agreement_across_multiple_drafts(self):
        accepted=self.accepted()
        first=self.draft(source_revision=str(accepted.pk))
        plan=self.plan(document_id=first.pk)
        second=self.draft(source_revision=str(accepted.pk))
        with self.assertRaises(ValidationError):self.plan(document_id=second.pk)
        self.assertEqual(plan.agreement_id,accepted.proposal_id)
        with self.assertRaises(ValidationError):self.plan(document_id=self.draft(source_revision=str(accepted.pk),lines=[{'description':'Too much','quantity':'1','rate':'1001'}]).pk)

    def test_source_agreement_must_belong_to_client_and_project(self):
        accepted=self.accepted()
        other=create_work(data=work_data(contact_email='different@example.invalid',project_name='Unrelated private project'),actor=self.actor)[0]
        with self.assertRaises(ValidationError):
            save_invoice_document(project_id=other.project_id,client_id=other.client_contact.client_id,
                data={**self.document.draft,'source_revision':str(accepted.pk)},token=uuid.uuid4(),version=0,actor=self.actor)
        draft=self.draft(source_revision=str(accepted.pk))
        with self.assertRaises(ValidationError):
            save_invoice_document(project_id=self.project.pk,client_id=self.account.pk,data={**draft.draft,'source_revision':''},
                token=draft.pk,version=1,actor=self.actor,document_id=draft.pk)

    def test_hourly_source_and_stale_agreement_cannot_become_plan(self):
        accepted=self.accepted()
        draft=self.draft(source_revision=str(accepted.pk))
        accepted.snapshot['pricing']='hourly';accepted.save()
        with self.assertRaises(ValidationError):self.plan(document_id=draft.pk)
        accepted.state='withdrawn';accepted.save()
        with self.assertRaises(ValidationError):self.plan(document_id=draft.pk)

    def test_deposit_progress_and_final_share_one_budget(self):
        plan=self.plan()
        deposit=self.invoice(plan,stage='deposit',amount='250')
        self.invoice(plan,stage='progress',amount='300')
        with self.assertRaises(ValidationError):self.invoice(plan,stage='deposit',amount='50')
        with self.assertRaises(ValidationError):self.invoice(plan,stage='final',amount='400')
        final=self.invoice(plan,stage='final',amount='450')
        self.assertEqual(plan_summary(plan)['remaining'],0)
        with self.assertRaises(ValidationError):self.invoice(plan,amount='.01')
        self.assertEqual([deposit.number,final.number],['TEST-INV-00001','TEST-INV-00003'])

    def test_rounding_allocation_sums_back_to_discount_tax_and_total(self):
        plan=self.plan(discount='133.37',tax_percent='6.25')
        rows=[self.invoice(plan,amount='100') for _ in range(8)]
        rows.append(self.invoice(plan,stage='final',amount=str(plan_summary(plan)['remaining'])))
        for field in ['subtotal','discount','tax','total']:
            self.assertEqual(sum(Decimal(row.snapshot[field]) for row in rows),Decimal(plan.snapshot[field]),field)
        for row in rows:self.assertEqual(Decimal(row.snapshot['subtotal'])-Decimal(row.snapshot['discount'])+Decimal(row.snapshot['tax']),row.amount)

    def test_void_releases_amount_and_allocation_without_reusing_number(self):
        plan=self.plan(discount='100',tax_percent='6.25')
        first=self.invoice(plan,amount='200');second=self.invoice(plan,amount='200')
        self.void(first)
        final=self.invoice(plan,stage='final',amount=str(plan_summary(plan)['remaining']))
        self.assertNotEqual(first.number,final.number)
        self.assertEqual(sum(Decimal(row.snapshot['tax']) for row in [second,final]),Decimal(plan.snapshot['tax']))
        self.assertEqual(plan_summary(plan)['remaining'],0)

    def test_invoice_retry_cannot_repurpose_nonce(self):
        plan=self.plan();token=uuid.uuid4()
        invoice=self.invoice(plan,token=token)
        self.assertEqual(self.invoice(plan,token=token).pk,invoice.pk)
        with self.assertRaises(ValidationError):self.invoice(plan,token=token,amount='101')
        self.assertEqual(NativeInvoice.objects.count(),1)

    def test_invoice_server_validation(self):
        plan=self.plan()
        for changes in [dict(amount='NaN'),dict(amount='0'),dict(amount='-1'),dict(amount='1.001'),dict(amount='1001'),
            dict(stage='unknown'),dict(description=''),dict(due_date=timezone.localdate()-timedelta(days=1)),
            dict(issue_date=timezone.localdate()+timedelta(days=1)),dict(issue_date='invalid')]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.invoice(plan,**changes)
        self.assertFalse(NativeInvoice.objects.exists())

    def test_payments_credits_and_reversals_preserve_history(self):
        plan=self.plan();invoice=self.invoice(plan)
        original=copy.deepcopy(invoice.snapshot)
        payment=self.entry(invoice,amount='40')
        credit=self.entry(invoice,kind='credit',method='',amount='10',note='Test adjustment')
        self.assertEqual(balance(invoice)['due'],50)
        self.assertEqual(credit.credit_number,'TEST-CR-00001')
        with self.assertRaises(ValidationError):self.entry(invoice,amount='50.01')
        self.entry(invoice,amount='50',method='cash')
        self.assertEqual(balance(invoice)['due'],0)
        self.reverse(invoice,payment)
        self.assertEqual(balance(invoice)['due'],40)
        self.reverse(invoice,credit)
        self.assertEqual(balance(invoice)['due'],50)
        self.assertEqual(NativeInvoiceEntry.objects.count(),5)
        self.assertEqual(plan_summary(plan)['remaining'],900)
        invoice.refresh_from_db();self.assertEqual(invoice.snapshot,original)

    def test_void_requires_no_active_payments_or_credits(self):
        plan=self.plan();invoice=self.invoice(plan)
        entry=self.entry(invoice)
        with self.assertRaises(ValidationError):self.void(invoice)
        self.reverse(invoice,entry);self.void(invoice)
        with self.assertRaises(ValidationError):self.entry(invoice)
        self.assertEqual(balance(NativeInvoice.objects.get(pk=invoice.pk))['status'],'Voided')
        self.assertEqual(plan_summary(plan)['remaining'],1000)

    def test_stale_version_and_token_checks(self):
        plan=self.plan();invoice=self.invoice(plan);token=uuid.uuid4()
        entry=self.entry(invoice,token=token)
        retry=self.entry(invoice,token=token,version=1)
        self.assertEqual(retry.pk,entry.pk)
        with self.assertRaises(ValidationError):self.entry(invoice,token=token,amount='26')
        with self.assertRaises(EditConflict):self.entry(invoice,version=1,reference='Different')
        reversal_token=uuid.uuid4();reversal=self.reverse(invoice,entry,token=reversal_token)
        self.assertEqual(self.reverse(invoice,entry,token=reversal_token).pk,reversal.pk)
        with self.assertRaises(ValidationError):self.reverse(invoice,entry)
        void_token=uuid.uuid4();self.void(invoice,token=void_token)
        self.assertEqual(self.void(invoice,token=void_token).pk,invoice.pk)

    def test_cross_invoice_reversal_and_invalid_entry_details(self):
        plan=self.plan();invoice=self.invoice(plan);other=self.invoice(plan)
        entry=self.entry(invoice)
        with self.assertRaises(ValidationError):self.reverse(other,entry)
        for changes in [dict(kind='refund'),dict(kind='credit',method='cash'),dict(method=''),dict(amount='0'),dict(note=''),
            dict(entry_date=timezone.localdate()+timedelta(days=1)),dict(entry_date=timezone.localdate()-timedelta(days=1))]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.entry(invoice,**changes)

    def test_overdue_and_credit_statuses(self):
        plan=self.plan();day=timezone.localdate()-timedelta(days=5)
        invoice=self.invoice(plan,issue_date=day,due_date=day+timedelta(days=1))
        self.assertEqual(balance(invoice)['status'],'Overdue')
        self.entry(invoice,kind='credit',method='',amount='100')
        self.assertEqual(balance(invoice)['status'],'Credited')

    def test_no_real_billing_notifications_or_public_access(self):
        counts=(FreshBooksInvoice.objects.count(),NotificationDelivery.objects.count())
        plan=self.plan();invoice=self.invoice(plan);self.entry(invoice)
        self.assertEqual(counts,(FreshBooksInvoice.objects.count(),NotificationDelivery.objects.count()))
        for name,arg in [('native_billing',None),('native_plan',plan.pk),('native_invoice',invoice.pk),('native_invoice_pdf',invoice.pk)]:
            url=reverse('workqueue:'+name,args=[arg] if arg else [])
            self.assertEqual(Browser().get(url).status_code,302)
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response['Cache-Control'])

    def test_read_permission_does_not_allow_accounting_writes(self):
        plan=self.plan();invoice=self.invoice(plan)
        viewer=get_user_model().objects.create_user('billing-viewer',is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('workqueue:native_invoice',args=[invoice.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('workqueue:native_invoice',args=[invoice.pk]),{'action':'void'}).status_code,403)
        self.assertEqual(self.client.get(reverse('workqueue:native_plan_start',args=[self.document.pk])).status_code,403)
        with self.assertRaises(PermissionDenied):self.plan(actor=viewer)
        with self.assertRaises(PermissionDenied):self.entry(invoice,actor=viewer)

    def test_plan_start_checkbox_then_invoice_percentage_form(self):
        url=reverse('workqueue:native_plan_start',args=[self.document.pk]);token=uuid.uuid4()
        data=dict(token=str(token),version='1',discount='100',tax_percent='0')
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertFalse(NativeBillingPlan.objects.exists())
        response=self.client.post(url,{**data,'confirm':'on'})
        self.assertEqual(response.status_code,302)
        plan=NativeBillingPlan.objects.get()
        self.assertEqual(self.client.get(url).url,reverse('workqueue:native_plan',args=[plan.pk]))
        issue=dict(token=str(uuid.uuid4()),stage='deposit',description='Initial deposit',basis='percent',value='25',
            issue_date=timezone.localdate().isoformat(),due_date=(timezone.localdate()+timedelta(days=30)).isoformat(),confirm='on')
        self.assertEqual(self.client.post(response.url,issue).status_code,302)
        invoice=NativeInvoice.objects.get()
        self.assertEqual(invoice.amount,Decimal('225'))
        self.assertContains(self.client.get(reverse('workqueue:native_invoice',args=[invoice.pk])),'TEST-INV-00001')

    def test_bad_action_and_invalid_stage_form_show_errors(self):
        plan=self.plan();invoice=self.invoice(plan)
        response=self.client.post(reverse('workqueue:native_invoice',args=[invoice.pk]),{'action':'unexpected'})
        self.assertContains(response,'Choose an invoice action.',status_code=400)
        response=self.client.post(reverse('workqueue:native_plan',args=[plan.pk]),dict(token=str(uuid.uuid4()),stage='progress',description='Test',basis='remaining',value='2',
            issue_date=timezone.localdate(),due_date=timezone.localdate(),confirm='on'))
        self.assertContains(response,'Leave this empty',status_code=400)

    def test_csrf_required(self):
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.actor)
        plan=self.plan();invoice=self.invoice(plan)
        for url in [reverse('workqueue:native_plan_start',args=[self.document.pk]),reverse('workqueue:native_plan',args=[plan.pk]),reverse('workqueue:native_invoice',args=[invoice.pk])]:
            self.assertEqual(browser.post(url,{'action':'void'}).status_code,403)

    def test_pdf_is_branded_test_only_and_excludes_private_payment_notes(self):
        plan=self.plan(discount='100',tax_percent='6.25');invoice=self.invoice(plan)
        self.entry(invoice,note='PRIVATE-ACCOUNTING-SECRET')
        response=self.client.get(reverse('workqueue:native_invoice_pdf',args=[invoice.pk]))
        reader=PdfReader(BytesIO(response.content));text='\n'.join(page.extract_text() for page in reader.pages)
        self.assertIn('TEST-INV-00001',text);self.assertIn('Provost Home Design',text)
        self.assertIn('Allocated tax',text);self.assertNotIn('PRIVATE-ACCOUNTING-SECRET',text)
        for page in reader.pages:self.assertIn('TEST ONLY - NOT FOR PAYMENT',page.extract_text())

    def test_search_and_escaping(self):
        plan=self.plan();invoice=self.invoice(plan,description='<script>bad()</script>')
        self.assertContains(self.client.get(reverse('workqueue:native_billing'),{'q':invoice.number}),invoice.number)
        self.assertNotContains(self.client.get(reverse('workqueue:native_billing'),{'q':'no-match-here'}),invoice.number)
        response=self.client.get(reverse('workqueue:native_invoice',args=[invoice.pk]))
        self.assertContains(response,'&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(response,'<script>bad()</script>')

    def test_small_installment_rounding_and_replacement_remain_balanced(self):
        plan=self.plan(discount='999.80',tax_percent='100')
        rows=[self.invoice(plan,amount='.01') for _ in range(21)]
        self.void(rows[0]);rows=rows[1:]
        rows.append(self.invoice(plan,stage='final',amount=str(plan_summary(plan)['remaining'])))
        for field in ['subtotal','discount','tax','total']:
            self.assertEqual(sum(Decimal(row.snapshot[field]) for row in rows),Decimal(plan.snapshot[field]))

    def test_entry_reverse_void_forms_and_paid_state(self):
        plan=self.plan();invoice=self.invoice(plan);url=reverse('workqueue:native_invoice',args=[invoice.pk])
        data={'action':'entry','entry-token':str(uuid.uuid4()),'entry-version':'1','entry-kind':'payment','entry-amount':'100',
            'entry-entry_date':str(timezone.localdate()),'entry-method':'cash','entry-reference':'','entry-note':'Fictional cash payment','entry-confirm':'on'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(balance(invoice)['status'],'Paid')
        entry=invoice.entries.get()
        reverse_data={'action':'reverse','reverse-token':str(uuid.uuid4()),'reverse-version':'2','reverse-entry':str(entry.pk),
            'reverse-note':'Correcting a test record','reverse-confirm':'on'}
        self.assertEqual(self.client.post(url,reverse_data).status_code,302)
        void_data={'action':'void','void-token':str(uuid.uuid4()),'void-version':'3','void-note':'End test','void-confirm':'on'}
        self.assertEqual(self.client.post(url,void_data).status_code,302)
        self.assertContains(self.client.get(url),'Voided')

    def test_accepted_source_is_preserved_by_new_invoice_editor(self):
        source=self.accepted()
        url=reverse('workqueue:invoice_document_new',args=[self.project.pk])
        response=self.client.get(url,{'proposal':source.proposal_id})
        self.assertEqual(str(response.context['form']['source_revision'].value()),str(source.pk))
        initial=response.context['form'].initial
        data={'client':str(self.account.pk),'title':'Accepted work bill','document_kind':'invoice','pricing':'fixed',
            'issue_date':str(timezone.localdate()),'due_date':str(timezone.localdate()+timedelta(days=30)),
            'token':str(initial['token']),'version':'0','source_revision':str(source.pk),
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-description':'Accepted work','lines-0-quantity':'1','lines-0-rate':'1000','lines-0-ORDER':'0'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        document=InvoiceDocument.objects.get(pk=initial['token'])
        self.assertEqual(document.source_revision_id,source.pk)
        self.assertEqual(str(self.client.get(reverse('workqueue:invoice_document_edit',args=[self.project.pk,document.pk])).context['form']['source_revision'].value()),str(source.pk))

    def test_close_plan_requires_voids_and_allows_replacement(self):
        source=self.accepted();document=self.draft(source_revision=str(source.pk))
        plan=self.plan(document_id=document.pk);invoice=self.invoice(plan)
        args=dict(plan_id=plan.pk,note='Correct plan pricing',token=uuid.uuid4(),actor=self.actor)
        with self.assertRaises(ValidationError):close_plan(**args)
        self.void(invoice);close_plan(**args)
        self.assertEqual(close_plan(**args).pk,plan.pk)
        with self.assertRaises(ValidationError):close_plan(**{**args,'note':'Repurpose token'})
        with self.assertRaises(ValidationError):self.invoice(plan)
        replacement=self.plan(document_id=document.pk,discount='50')
        self.assertNotEqual(plan.pk,replacement.pk)
        self.assertEqual(plan.invoices.count(),1)
        self.assertEqual(NativeBillingPlan.objects.filter(agreement=source.proposal,closed_at__isnull=True).count(),1)

    def test_close_plan_form_and_frozen_history(self):
        plan=self.plan();url=reverse('workqueue:native_plan',args=[plan.pk])
        data={'action':'close','close-token':str(uuid.uuid4()),'close-note':'Replace with corrected tax','close-confirm':'on'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        response=self.client.get(url)
        self.assertContains(response,'Prepare a replacement test plan')
        self.assertNotContains(response,'Create the next test invoice')
        self.assertContains(response,'Replace with corrected tax')
        self.assertEqual(self.client.get(reverse('workqueue:native_plan_start',args=[self.document.pk])).status_code,200)


@skipUnless(connection.vendor=='postgresql','Production concurrency is verified on PostgreSQL.')
@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False,INTAKE_PUBLIC_BASE_URL='https://testserver')
class NativeInvoiceConcurrencyTests(InvoiceFixtures,TransactionTestCase):
    def setUp(self):self.seed_billing()

    def race(self,call):
        def run(_):
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=self.actor.pk)
                return call(actor)
            except (ValidationError,EditConflict):return None
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,range(2)))

    def test_two_final_invoices_cannot_overbill_or_duplicate_number(self):
        plan=self.plan()
        results=self.race(lambda actor:self.invoice(plan,actor=actor,stage='final',amount='1000').pk)
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(plan_summary(plan)['remaining'],0)

    def test_two_payments_cannot_overpay(self):
        plan=self.plan();invoice=self.invoice(plan)
        results=self.race(lambda actor:self.entry(invoice,actor=actor,version=1,amount='75').pk)
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(balance(invoice)['due'],25)

    def test_simultaneous_same_token_is_one_invoice(self):
        plan=self.plan();token=uuid.uuid4()
        results=self.race(lambda actor:self.invoice(plan,actor=actor,token=token).pk)
        self.assertEqual(len(set(results)),1);self.assertNotIn(None,results)
        self.assertEqual(NativeInvoice.objects.count(),1)

    def test_same_source_plan_is_created_once(self):
        token=uuid.uuid4()
        results=self.race(lambda actor:self.plan(actor=actor,token=token).pk)
        self.assertEqual(len(set(results)),1);self.assertNotIn(None,results)
        self.assertEqual(NativeBillingPlan.objects.count(),1)
