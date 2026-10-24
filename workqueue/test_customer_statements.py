from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from .customer_statements import statement_for
from .customer_statement_pdf import statement_pdf
from .models import Client, NotificationDelivery
from .test_native_invoices import InvoiceFixtures


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class CustomerStatementTests(InvoiceFixtures, TestCase):
    def setUp(self):
        self.seed_billing()
        self.client.force_login(self.actor)

    def statement(self):
        return statement_for(self.account, actor=self.actor)

    def test_empty_statement_is_not_real_balance(self):
        result = self.statement()
        self.assertEqual(result['due'], Decimal('0.00'))
        self.assertEqual(result['activity'], [])
        response = self.client.get(reverse('workqueue:customer_statement', args=[self.account.pk]))
        self.assertContains(response, 'FreshBooks invoices and balances are excluded')
        self.assertContains(response, 'No test activity')

    def test_payments_credits_and_reversals_reconcile(self):
        invoice = self.invoice(self.plan(), amount='500')
        payment = self.entry(invoice, amount='100')
        self.entry(invoice, kind='credit', amount='50', method='')
        self.reverse(invoice, payment)
        self.entry(invoice, amount='200')
        result = self.statement()
        self.assertEqual((result['paid'], result['credited'], result['due']), (Decimal('200'), Decimal('50'), Decimal('250')))
        self.assertEqual(sum(row['amount'] for row in result['activity']), result['due'])
        self.assertEqual(result['activity'][-1]['running_balance'], result['due'])
        self.assertIn('Payment reversal', [row['kind'] for row in result['activity']])

    def test_void_keeps_original_history_and_zero_balance(self):
        invoice = self.invoice(self.plan())
        credit = self.entry(invoice, kind='credit', method='')
        self.reverse(invoice, credit)
        self.void(invoice)
        result = self.statement()
        self.assertEqual(result['net_invoiced'], 0)
        self.assertEqual(result['due'], 0)
        self.assertEqual(result['open_invoices'], [])
        self.assertEqual([row['kind'] for row in result['activity']], ['Invoice', 'Credit', 'Credit reversal', 'Invoice void'])

    def test_aging_boundaries_and_partial_balances(self):
        plan = self.plan()
        today = timezone.localdate()
        for days in [0, 1, 30, 31, 60, 61, 90, 91]:
            invoice = self.invoice(plan, issue_date=today-timedelta(days=100), due_date=today-timedelta(days=days))
            self.entry(invoice, amount='25')
        result = self.statement()
        self.assertEqual([amount for label,amount in result['aging']], [Decimal('75'), Decimal('150'), Decimal('150'), Decimal('150'), Decimal('75')])
        self.assertEqual(sum(amount for label,amount in result['aging']), result['due'])

    def test_backdated_entries_sort_by_effective_date(self):
        today = timezone.localdate()
        invoice = self.invoice(self.plan(), issue_date=today-timedelta(days=20))
        payment = self.entry(invoice, entry_date=today-timedelta(days=2))
        self.reverse(invoice, payment)
        self.entry(invoice, entry_date=today-timedelta(days=10))
        activity = self.statement()['activity']
        self.assertEqual([row['date'] for row in activity], sorted(row['date'] for row in activity))
        self.assertEqual(activity[-1]['running_balance'], Decimal('75'))

    def test_merge_follows_explicit_identity_not_name_or_email(self):
        self.invoice(self.plan())
        unrelated = Client.objects.create(name=self.account.name, normalized_name=self.account.normalized_name)
        self.assertEqual(statement_for(unrelated, actor=self.actor)['due'], 0)
        target = Client.objects.create(name='Merged contractor', normalized_name='merged contractor')
        middle = Client.objects.create(name='Earlier record', normalized_name='earlier record', merged_into=target)
        self.account.merged_into=middle;self.account.save()
        result = statement_for(target, actor=self.actor)
        self.assertEqual(result['due'], Decimal('100'))
        self.assertEqual(self.statement()['client_id'], target.pk)
        response = self.client.get(reverse('workqueue:customer_statement', args=[self.account.pk]))
        self.assertRedirects(response, reverse('workqueue:customer_statement', args=[target.pk]))

    def test_safe_projection_and_pdf_exclude_internal_details(self):
        invoice = self.invoice(self.plan())
        self.entry(invoice, note='SECRET PRIVATE NOTE', reference='SECRET BANK REFERENCE')
        self.account.internal_notes='SECRET CRM NOTE';self.account.save()
        result = self.statement()
        self.assertNotIn('SECRET', str(result))
        pdf = statement_pdf(result)
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn('Customer statement', text)
        self.assertIn('TEST ONLY', text)
        self.assertIn('$75.00', text)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('TEST-CHECK', text)

    def test_pdf_multipage_repeats_headers_and_footer(self):
        self.invoice(self.plan())
        result = self.statement()
        result['activity'] = result['activity'] * 100
        pdf = PdfReader(BytesIO(statement_pdf(result)))
        self.assertGreater(len(pdf.pages), 2)
        for page in pdf.pages:
            self.assertIn('NOT FOR PAYMENT', page.extract_text())
        self.assertIn('ACTIVITY / PROJECT', pdf.pages[-1].extract_text())

    def test_auth_permissions_private_headers_and_get_only(self):
        urls = [reverse('workqueue:customer_statements'), reverse('workqueue:customer_statement', args=[self.account.pk]), reverse('workqueue:customer_statement_pdf', args=[self.account.pk])]
        for url in urls:
            response=self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('noindex', response['X-Robots-Tag'])
            self.assertEqual(self.client.post(url).status_code, 405)
        self.client.logout()
        for url in urls:self.assertEqual(self.client.get(url).status_code,302)
        viewer=get_user_model().objects.create_user('statement-viewer', is_staff=True)
        self.client.force_login(viewer)
        for url in urls:self.assertEqual(self.client.get(url).status_code,403)
        with self.assertRaises(PermissionDenied):statement_for(self.account, actor=viewer)
        viewer.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        viewer=get_user_model().objects.get(pk=viewer.pk)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(urls[1]).status_code,200)

    def test_search_and_unknown_client_and_no_email_side_effect(self):
        before=NotificationDelivery.objects.count()
        url=reverse('workqueue:customer_statements')
        self.assertContains(self.client.get(url, {'q': self.account.name}), self.account.name)
        self.assertContains(self.client.get(url, {'q': 'no-such-client'}), 'No matching clients')
        import uuid
        self.assertEqual(self.client.get(reverse('workqueue:customer_statement',args=[uuid.uuid4()])).status_code,404)
        self.client.get(reverse('workqueue:customer_statement_pdf',args=[self.account.pk]))
        self.assertEqual(NotificationDelivery.objects.count(),before)
