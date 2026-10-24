"""Invoice-scoped owner-only trial checkout. No staff identity is granted."""
import copy
from datetime import timedelta
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from .invoice_delivery import active_delivery,owner_test_address
from .invoice_delivery_models import NativeInvoiceDelivery
from .native_invoices import balance,next_number
from .services import lock_queue
from .test_payment_gateway import configuration,TestStripeGateway
from .test_payment_models import NativePaymentAttempt,RESERVED_STATES
from .client_payment_models import NativeTestPaymentReceipt


def require_checkout_enabled():
    if not configuration()['client_checkout_enabled']:
        raise ValidationError('Private-link sandbox checkout is paused. FreshBooks still handles real billing.')


def authorized_delivery(delivery_id,*,write=False):
    delivery=NativeInvoiceDelivery.objects.select_related('invoice').get(pk=delivery_id)
    if not active_delivery(delivery) or not delivery.allow_test_checkout:raise PermissionDenied
    if write:
        require_checkout_enabled()
        if delivery.owner_recipient.lower()!=owner_test_address():
            raise ValidationError('The owner test inbox changed. Ask for a new reviewed test copy.')
    return delivery


@transaction.atomic
def reserve_for_delivery(*,delivery_id,version,method,token,account_id):
    from .test_payments import _reserve
    lock_queue();delivery=authorized_delivery(delivery_id,write=True)
    existing=NativePaymentAttempt.objects.filter(pk=token).first()
    if not existing and delivery.payment_attempts.filter(created_at__gte=timezone.now()-timedelta(hours=1)).count()>=5:
        raise ValidationError('Too many test checkout attempts on this private copy. Please wait or contact Provost Home Design.')
    return _reserve(invoice_id=delivery.invoice_id,version=version,method=method,token=token,
                    account_id=account_id,delivery=delivery)


def start_for_delivery(*,delivery_id,version,method,token,gateway=None):
    from .test_payments import create_or_resume
    # Authorization before contacting Stripe. Recheck under the write lock below.
    authorized_delivery(delivery_id,write=True)
    gateway=gateway or TestStripeGateway()
    attempt=reserve_for_delivery(delivery_id=delivery_id,version=version,method=method,token=token,account_id=gateway.account_id())
    return create_or_resume(attempt,gateway=gateway)


def change_attempt(*,delivery_id,attempt_id,action,gateway=None):
    from .test_payments import create_or_resume,refresh
    delivery=authorized_delivery(delivery_id,write=True)
    attempt=NativePaymentAttempt.objects.get(pk=attempt_id,delivery=delivery,invoice=delivery.invoice)
    if action not in {'refresh','continue','retry','expire'}:raise ValidationError('Choose a test payment action.')
    if action=='expire' and attempt.state!='open':
        raise ValidationError('Only an unused open checkout can be closed. Pending bank payments remain reserved.')
    if action=='retry':return create_or_resume(attempt,gateway=gateway)
    return refresh(attempt,gateway=gateway,expire=action=='expire')


def invoice_payment_context(delivery):
    # Permission to see this invoice never exposes Stripe references or staff actions.
    own=delivery.payment_attempts.order_by('-created_at')
    from .test_refunds import refund_hold
    held=refund_hold(delivery.invoice) or NativePaymentAttempt.objects.filter(invoice=delivery.invoice,state__in=RESERVED_STATES).exists()
    return {'client_payment_config':configuration(),'client_attempts':own,
        'client_payment_hold':held,
        'client_receipts':NativeTestPaymentReceipt.objects.filter(attempt__invoice=delivery.invoice).order_by('-created_at')}


@transaction.atomic
def ensure_receipt(attempt):
    lock_queue()
    attempt=NativePaymentAttempt.objects.select_related('invoice','ledger_entry','delivery').get(pk=attempt.pk)
    existing=NativeTestPaymentReceipt.objects.filter(attempt=attempt).first()
    if existing:return existing
    if not attempt.delivery_id or not attempt.ledger_entry_id or attempt.state!='paid' or attempt.issue_code:return None
    entry=attempt.ledger_entry
    if entry.invoice_id!=attempt.invoice_id or entry.kind!='payment' or entry.amount!=attempt.amount:
        raise ValidationError('The verified payment does not match its receipt.')
    invoice=attempt.invoice;snapshot=invoice.snapshot
    receipt=NativeTestPaymentReceipt(number=next_number('last_receipt','RC'),attempt=attempt,snapshot={
        'issuer':copy.deepcopy(snapshot.get('issuer',{})),
        'invoice_number':invoice.number,'project_name':snapshot.get('project_name',''),
        'client_name':snapshot.get('client_name',''),'client_address':snapshot.get('billing_address',''),
        'amount':str(entry.amount),'currency':'USD','method':attempt.get_method_display(),
        'payment_date':entry.date.isoformat(),'recorded_at':timezone.localtime(entry.created_at).isoformat(),
        'test_only':True})
    receipt.full_clean();receipt.save()
    from .native_invoice_models import NativeInvoiceEvent
    NativeInvoiceEvent.objects.create(invoice=invoice,action='Test payment receipt recorded',
        details={'receipt':str(receipt.pk),'number':receipt.number,'attempt':str(attempt.pk)})
    return receipt


def receipt_projection(receipt):
    # Public/PDF allow-list. Private notes, references and identifiers are excluded.
    snapshot=receipt.snapshot
    return {'number':receipt.number,**{key:snapshot.get(key,'') for key in (
        'issuer','invoice_number','project_name','client_name','client_address','amount','currency','method','payment_date','recorded_at')}}
