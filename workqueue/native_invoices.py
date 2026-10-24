"""Serialized, append-only trial ledger. No email, gateway or FreshBooks writes."""
import copy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .crm import require_staff
from .document_models import InvoiceDocument
from .native_invoice_models import NativeBillingPlan, NativeInvoice, NativeInvoiceEntry, NativeInvoiceEvent, NativeInvoiceSequence
from .pm_services import event
from .services import EditConflict, lock_queue

CENT = Decimal('.01')
MAXIMUM = Decimal('999999999.99')


def money(value, *, zero=False, maximum=MAXIMUM):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or (not zero and value == 0) or value > maximum or value != value.quantize(CENT):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Enter a valid amount with at most two decimal places.') from None
    return value.quantize(CENT)


def checked_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('Enter a valid date.') from None


def text(value, maximum, label, *, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValidationError('Review '+label+'.')
    return value.strip()


def same_token(existing, actor, **values):
    if existing.actor_id != actor.pk or any(getattr(existing, key) != value for key, value in values.items()):
        raise ValidationError('This submission reference was already used for a different action. Reload the page.')
    return existing


def plan_summary(plan):
    billed = sum(plan.invoices.filter(voided_at__isnull=True).values_list('amount', flat=True), Decimal('0.00'))
    return {'total':plan.total, 'billed':billed, 'remaining':plan.total-billed}


def balance(invoice):
    entries = list(invoice.entries.all())
    reversed_ids = {entry.reverses_id for entry in entries if entry.kind == 'reversal'}
    paid = sum((entry.amount for entry in entries if entry.kind == 'payment' and entry.pk not in reversed_ids), Decimal('0.00'))
    credited = sum((entry.amount for entry in entries if entry.kind == 'credit' and entry.pk not in reversed_ids), Decimal('0.00'))
    due = invoice.amount-paid-credited if not invoice.voided_at else Decimal('0.00')
    status = ('Voided' if invoice.voided_at else 'Credited' if credited == invoice.amount else 'Paid' if paid == invoice.amount else 'Settled' if due == 0
        else 'Overdue' if invoice.due_date < timezone.localdate() else 'Partially paid' if paid or credited else 'Open')
    return {'paid':paid, 'credited':credited, 'due':due, 'status':status, 'reversed_ids':reversed_ids}


@transaction.atomic
def create_plan(*, document_id, version, discount, tax_percent, token, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    discount = money(discount, zero=True)
    tax_percent = money(tax_percent, zero=True, maximum=Decimal('100'))
    existing = NativeBillingPlan.objects.filter(pk=token).first()
    if existing:
        same_token(existing, actor, document_id=document_id)
        if existing.revision.sequence != version or existing.snapshot['discount'] != str(discount) or existing.snapshot['tax_percent'] != str(tax_percent):
            raise ValidationError('This submission reference belongs to different billing details.')
        return existing
    document = InvoiceDocument.objects.select_related('source_revision__proposal').get(pk=document_id)
    if document.version != version:
        raise EditConflict('The invoice draft changed. Reload and review it before creating a test billing plan.')
    if NativeBillingPlan.objects.filter(document=document,closed_at__isnull=True).exists():
        raise ValidationError('This draft already has a test billing plan. Open that plan to continue.')
    revision = document.revisions.get(sequence=version)
    snapshot = copy.deepcopy(revision.snapshot)
    subtotal = money(snapshot['total'])
    if snapshot.get('pricing') != 'fixed':
        raise ValidationError('Prepare a fixed amount for the work to bill first.')
    agreement = None
    if document.source_revision_id:
        source = document.source_revision
        if source.state != 'accepted' or source.proposal.current_revision_id != source.pk or source.proposal.has_draft:
            raise ValidationError('The source agreement changed. Prepare a new draft from the current accepted version.')
        if source.snapshot.get('pricing') != 'fixed' or subtotal > money(source.snapshot['total']):
            raise ValidationError('Use a fixed price no greater than the selected accepted agreement. Bill approved change orders separately.')
        agreement = source.proposal
        if NativeBillingPlan.objects.filter(agreement=agreement,closed_at__isnull=True).exists():
            raise ValidationError('This agreement already has a test billing plan. Use that plan to prevent duplicate billing.')
    if discount >= subtotal:
        raise ValidationError('The discount must be less than the service subtotal.')
    tax = ((subtotal-discount)*tax_percent/100).quantize(CENT, rounding=ROUND_HALF_UP)
    total = money(subtotal-discount+tax)
    snapshot.update(subtotal=str(subtotal),discount=str(discount),tax_percent=str(tax_percent),tax=str(tax),total=str(total),
        agreement_number=agreement.number if agreement else '',agreement_revision=str(document.source_revision_id or ''),
        draft_version=version,document_number=document.number)
    plan = NativeBillingPlan(id=token,document=document,revision=revision,agreement=agreement,snapshot=snapshot,total=total,actor=actor)
    plan.full_clean();plan.save()
    event(document.project,actor,'Test billing plan created',plan=str(plan.pk),draft=document.number,total=str(total),test_only=True)
    return plan


def next_number(field, prefix):
    sequence, _ = NativeInvoiceSequence.objects.get_or_create(pk=1)
    value = getattr(sequence,field)+1
    setattr(sequence,field,value);sequence.save(update_fields=[field])
    return f'TEST-{prefix}-{value:05d}'


@transaction.atomic
def issue_test_invoice(*, plan_id, stage, amount, description, issue_date, due_date, token, actor):
    require_staff(actor,'workqueue.change_workitem')
    lock_queue()
    amount = money(amount);issued = checked_date(issue_date);due = checked_date(due_date)
    description = text(description,200,'the billing description')
    if stage not in {'deposit','progress','final'} or due < issued or issued > timezone.localdate():
        raise ValidationError('Review the billing stage and dates. The issue date cannot be in the future.')
    existing = NativeInvoice.objects.filter(pk=token).first()
    if existing:
        return same_token(existing,actor,plan_id=plan_id,stage=stage,amount=amount,description=description,issue_date=issued,due_date=due)
    plan = NativeBillingPlan.objects.select_related('document__project').get(pk=plan_id)
    if plan.closed_at:
        raise ValidationError('This test billing plan is closed. Start a new plan from the working draft.')
    remaining = plan_summary(plan)['remaining']
    if amount > remaining:
        raise ValidationError('This exceeds the unbilled plan amount. Reload to see the remaining amount.')
    active = plan.invoices.filter(voided_at__isnull=True)
    if stage == 'deposit' and active.exists():
        raise ValidationError('A deposit must be the first invoice in this plan. Use a progress or final invoice.')
    if stage == 'final' and amount != remaining:
        raise ValidationError('A final invoice must bill the entire remaining plan amount.')
    number = next_number('last_invoice','INV')
    snapshot = copy.deepcopy(plan.snapshot)
    # Allocate the plan's tax and discount across installments. The last one
    # absorbs rounding cents; voided installments release their allocations.
    previous = list(active)
    tax_left = Decimal(snapshot['tax'])-sum((Decimal(row.snapshot['tax']) for row in previous),Decimal('0'))
    discount_left = Decimal(snapshot['discount'])-sum((Decimal(row.snapshot['discount']) for row in previous),Decimal('0'))
    if amount == remaining:
        tax,discount = tax_left,discount_left
    else:
        target_tax = (Decimal(snapshot['tax'])*amount/plan.total).quantize(CENT,rounding=ROUND_HALF_UP)
        tax = min(amount,tax_left,max(Decimal('0'),amount-(remaining-tax_left),target_tax))
        discount = min(discount_left,(Decimal(snapshot['discount'])*amount/plan.total).quantize(CENT,rounding=ROUND_HALF_UP))
    subtotal = amount-tax+discount
    snapshot.update(number=number,total=str(amount),issue_date=issued.isoformat(),due_date=due.isoformat(),
        billing_stage=stage,plan_reference=str(plan.pk),plan_total=str(plan.total),plan_billed_before=str(plan.total-remaining),
        plan_subtotal=snapshot['subtotal'],plan_discount=snapshot['discount'],plan_tax=snapshot['tax'],
        subtotal=str(subtotal),discount=str(discount),tax=str(tax),
        description=description,lines=[{'description':description,'quantity':'1','rate':str(subtotal),'amount':str(subtotal)}])
    invoice = NativeInvoice(id=token,plan=plan,number=number,stage=stage,amount=amount,description=description,
        issue_date=issued,due_date=due,snapshot=snapshot,actor=actor)
    invoice.full_clean();invoice.save()
    NativeInvoiceEvent.objects.create(invoice=invoice,action='Test invoice created',actor=actor,details={'amount':str(amount),'stage':stage})
    event(plan.document.project,actor,'Test invoice created',number=number,amount=str(amount),test_only=True)
    return invoice


@transaction.atomic
def close_plan(*, plan_id, note, token, actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    note=text(note,2000,'the reason for closing this test plan')
    existing=NativeBillingPlan.objects.filter(closed_token=token).first()
    if existing:
        if existing.pk!=plan_id or existing.closed_by_id!=actor.pk or existing.closed_note!=note:
            raise ValidationError('This submission reference belongs to another plan closure.')
        return existing
    plan=NativeBillingPlan.objects.select_related('document__project').get(pk=plan_id)
    if plan.closed_at:
        raise ValidationError('This test plan has already been closed.')
    if plan.invoices.filter(voided_at__isnull=True).exists():
        raise ValidationError('Void all invoices in this plan before closing it. Reverse active payments and credits first.')
    plan.closed_at=timezone.now();plan.closed_by=actor;plan.closed_token=token;plan.closed_note=note
    plan.full_clean();plan.save(update_fields=['closed_at','closed_by','closed_token','closed_note'])
    event(plan.document.project,actor,'Test billing plan closed',plan=str(plan.pk),note=note,test_only=True)
    return plan


def checked_invoice(invoice_id,version):
    invoice = NativeInvoice.objects.get(pk=invoice_id)
    if invoice.version != version:
        raise EditConflict('This invoice changed. Reload before recording another action.')
    if invoice.voided_at:
        raise ValidationError('A voided invoice cannot receive payments or credits.')
    from .test_payments import assert_not_reserved
    assert_not_reserved(invoice)
    return invoice


@transaction.atomic
def record_entry(*, invoice_id, version, kind, amount, entry_date, method, reference, note, token, actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    amount = money(amount);day = checked_date(entry_date)
    reference = text(reference,100,'the payment reference',required=False)
    note = text(note,2000,'the payment or credit note')
    if kind not in {'payment','credit'} or (kind=='payment' and method not in NativeInvoiceEntry.Method.values) or (kind=='credit' and method):
        raise ValidationError('Choose a payment method, or a credit without a payment method.')
    existing = NativeInvoiceEntry.objects.filter(pk=token).first()
    if existing:
        return same_token(existing,actor,invoice_id=invoice_id,kind=kind,amount=amount,date=day,method=method,reference=reference,note=note)
    invoice = checked_invoice(invoice_id,version)
    if day < invoice.issue_date or day > timezone.localdate():
        raise ValidationError('The entry date must be between the invoice date and today.')
    if amount > balance(invoice)['due']:
        raise ValidationError('This exceeds the amount still due. Reload and review the balance.')
    entry = NativeInvoiceEntry(id=token,invoice=invoice,kind=kind,amount=amount,date=day,method=method,reference=reference,note=note,
        credit_number=next_number('last_credit','CR') if kind=='credit' else None,actor=actor)
    entry.full_clean();entry.save()
    invoice.version += 1;invoice.save(update_fields=['version'])
    NativeInvoiceEvent.objects.create(invoice=invoice,action='Test '+kind+' recorded',actor=actor,
        details={'entry':str(entry.pk),'amount':str(amount),'date':str(day),'method':method,'reference':reference,'note':note})
    return entry


@transaction.atomic
def reverse_entry(*, invoice_id, entry_id, version, note, token, actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    note = text(note,2000,'the reversal reason')
    existing = NativeInvoiceEntry.objects.filter(pk=token).first()
    if existing:
        return same_token(existing,actor,invoice_id=invoice_id,reverses_id=entry_id,kind='reversal',note=note)
    invoice = checked_invoice(invoice_id,version)
    entry = NativeInvoiceEntry.objects.filter(pk=entry_id,invoice=invoice,kind__in=['payment','credit']).first()
    if not entry or NativeInvoiceEntry.objects.filter(reverses=entry).exists():
        raise ValidationError('Choose an unreversed payment or credit from this invoice.')
    if hasattr(entry, 'stripe_test_attempt'):
        raise ValidationError('This payment was confirmed by Stripe. A manual reversal is not a refund; review its sandbox payment status instead.')
    reversal = NativeInvoiceEntry(id=token,invoice=invoice,kind='reversal',amount=entry.amount,date=timezone.localdate(),
        reverses=entry,note=note,actor=actor)
    reversal.full_clean();reversal.save()
    invoice.version += 1;invoice.save(update_fields=['version'])
    NativeInvoiceEvent.objects.create(invoice=invoice,action='Test entry reversed',actor=actor,
        details={'entry':str(entry.pk),'reversal':str(reversal.pk),'note':note})
    return reversal


@transaction.atomic
def void_invoice(*, invoice_id, version, note, token, actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    note = text(note,2000,'the void reason')
    existing = NativeInvoiceEvent.objects.filter(pk=token).first()
    if existing:
        if existing.details.get('note') != note or existing.action != 'Test invoice voided':
            raise ValidationError('This submission reference belongs to another action.')
        return same_token(existing,actor,invoice_id=invoice_id).invoice
    invoice = checked_invoice(invoice_id,version)
    amounts = balance(invoice)
    if amounts['paid'] or amounts['credited']:
        raise ValidationError('Reverse active payments and credits before voiding this invoice.')
    invoice.voided_at = timezone.now();invoice.version += 1;invoice.save(update_fields=['voided_at','version'])
    NativeInvoiceEvent.objects.create(id=token,invoice=invoice,action='Test invoice voided',actor=actor,details={'note':note})
    return invoice
