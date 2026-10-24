"""Durable, bounded, owner-only receipt emails. Accounting never depends on email."""
import uuid
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from .billing_mail_models import NativeBillingMailSettings,NativeReceiptEmail,NativeBillingMailEvent
from .client_payment_models import NativeTestPaymentReceipt
from .client_payments import receipt_projection
from .invoice_delivery import owner_test_address,active_delivery
from .models import NotificationDelivery
from .services import lock_queue,EditConflict

PREFIX='test-receipt-'


class ReceiptDocumentError(Exception):
    pass


def require_owner(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.is_staff or not actor.is_superuser:
        raise PermissionDenied


def current_settings():
    return NativeBillingMailSettings.objects.filter(pk=1).first() or NativeBillingMailSettings(pk=1)


@transaction.atomic
def save_settings(*,actor,version,automatic_receipts):
    require_owner(actor);lock_queue();config=current_settings()
    if config.version!=version:raise EditConflict('These settings changed. Reload before saving.')
    if config.automatic_receipts!=automatic_receipts:
        config.enabled_since=timezone.now() if automatic_receipts else None
    config.automatic_receipts=automatic_receipts;config.version+=1
    config.changed_at=timezone.now();config.changed_by=actor;config.full_clean();config.save()
    NativeBillingMailEvent.objects.create(action='Automatic sandbox receipts enabled' if automatic_receipts else 'Automatic sandbox receipts paused',
        actor=actor,details={'version':config.version,'future_only':True})
    if not automatic_receipts:
        for delivery in NativeReceiptEmail.objects.filter(automatic=True,notification__state='pending'):
            notice=NotificationDelivery.objects.select_for_update().get(pk=delivery.notification_id)
            if notice.state=='pending':_cancel(notice,'Automatic receipt emails were paused.')
    return config


def eligible(receipt):
    attempt=receipt.attempt;delivery=attempt.delivery
    return bool(delivery and active_delivery(delivery) and attempt.state=='paid' and not attempt.issue_code
        and attempt.ledger_entry_id and attempt.ledger_entry.invoice_id==attempt.invoice_id
        and attempt.ledger_entry.kind=='payment' and attempt.ledger_entry.amount==attempt.amount
        and receipt.snapshot.get('test_only') is True
        and receipt.number.startswith('TEST-RC-') and delivery.owner_recipient.lower()==owner_test_address())


def receipt_query():
    return NativeTestPaymentReceipt.objects.select_related('attempt__delivery__invoice','attempt__ledger_entry','attempt__invoice')


def message_context(receipt):
    return {'receipt':receipt_projection(receipt),'recipient':owner_test_address()}


@transaction.atomic
def queue_receipt(*,receipt_id,actor=None,automatic=False):
    if not automatic:require_owner(actor)
    lock_queue()
    receipt=receipt_query().get(pk=receipt_id)
    existing=NativeReceiptEmail.objects.filter(receipt=receipt).first()
    if existing:return existing
    if not eligible(receipt):raise ValidationError('This receipt is not eligible for owner test email. Its payment or original private delivery needs review.')
    if automatic:
        config=current_settings()
        if not config.automatic_receipts or not config.enabled_since or receipt.created_at<config.enabled_since:return None
    context=message_context(receipt);recipient=context['recipient']
    notice=NotificationDelivery.objects.create(recipient_kind='owner',recipient=recipient,
        subject=f'[TEST ONLY] Receipt {receipt.number} - {receipt.snapshot["invoice_number"]}',
        body=render_to_string('workqueue/payment_receipt_email.txt',context),
        html_body=render_to_string('workqueue/payment_receipt_email.html',context),provider_reference=PREFIX+uuid.uuid4().hex)
    delivery=NativeReceiptEmail.objects.create(receipt=receipt,notification=notice,owner_recipient=recipient,automatic=automatic,actor=actor)
    NativeBillingMailEvent.objects.create(delivery=delivery,actor=actor,action='Sandbox receipt email queued',details={'automatic':automatic,'receipt':receipt.number})
    return delivery


def queue_due_receipts(limit=100):
    config=current_settings()
    if not config.automatic_receipts or not config.enabled_since:return 0
    try:owner=owner_test_address()
    except ValidationError:return 0
    receipts=receipt_query().filter(created_at__gte=config.enabled_since,email_delivery__isnull=True,
        attempt__state='paid',attempt__issue_code='',attempt__delivery__revoked_at__isnull=True,
        attempt__delivery__expires_at__gt=timezone.now(),attempt__delivery__owner_recipient__iexact=owner,
        attempt__invoice__voided_at__isnull=True).order_by('created_at','pk')[:max(1,min(limit,100))]
    count=0
    for receipt in receipts:
        try:
            if eligible(receipt) and queue_receipt(receipt_id=receipt.pk,automatic=True):count+=1
        except ValidationError:
            continue
    return count


def _cancel(notice,reason):
    notice.state='canceled';notice.body='';notice.html_body='';notice.last_error=reason
    notice.save(update_fields=['state','body','html_body','last_error'])


def cancel_ineligible_receipt_notice(notice):
    # Called with the outbox row locked. Do not acquire QueueState in this order.
    if not notice.provider_reference.startswith(PREFIX):return False
    delivery=NativeReceiptEmail.objects.select_related('receipt__attempt__delivery__invoice','receipt__attempt__ledger_entry').filter(notification=notice).first()
    try:
        valid=bool(delivery and eligible(delivery.receipt) and notice.recipient.lower()==delivery.owner_recipient.lower()==owner_test_address()
            and not notice.to_recipients and not notice.cc_recipients and not notice.bcc_recipients)
        if valid and delivery.automatic:
            config=current_settings()
            valid=bool(config.automatic_receipts and config.enabled_since and delivery.receipt.created_at>=config.enabled_since)
    except ValidationError:valid=False
    if valid:return False
    _cancel(notice,'Receipt email canceled: its original authorization, owner inbox or automatic setting changed.')
    return True


def receipt_attachment(notice):
    if not notice.provider_reference.startswith(PREFIX):return None
    try:
        delivery=NativeReceiptEmail.objects.select_related('receipt').get(notification=notice)
        from .payment_receipt_pdf import receipt_pdf
        return (delivery.receipt.number+'-test-only.pdf',receipt_pdf(receipt_projection(delivery.receipt)),'application/pdf')
    except Exception:
        raise ReceiptDocumentError('The receipt PDF could not be prepared; no email was sent.') from None


@transaction.atomic
def delivery_action(*,delivery_id,actor,action,expected_state,attempts):
    require_owner(actor);lock_queue()
    delivery=NativeReceiptEmail.objects.select_related('receipt__attempt__delivery__invoice','receipt__attempt__ledger_entry').get(pk=delivery_id)
    notice=NotificationDelivery.objects.select_for_update().get(pk=delivery.notification_id)
    if notice.state!=expected_state or notice.attempts!=attempts:raise EditConflict('This email changed. Reload before choosing an action.')
    if action=='retry' and notice.state=='failed':
        if not eligible(delivery.receipt) or delivery.owner_recipient.lower()!=owner_test_address():raise ValidationError('The original test delivery is no longer eligible.')
        if delivery.automatic and not current_settings().automatic_receipts:raise ValidationError('Automatic receipts are paused. No retry was queued.')
        context=message_context(delivery.receipt)
        notice.body=render_to_string('workqueue/payment_receipt_email.txt',context);notice.html_body=render_to_string('workqueue/payment_receipt_email.html',context)
        notice.state='pending';notice.last_error=''
    elif action=='cancel' and notice.state in {'pending','failed','unknown'}:
        _cancel(notice,'Canceled after owner review.')
    elif action=='confirm_sent' and notice.state=='unknown':
        notice.state='sent';notice.sent_at=timezone.now();notice.last_error='Delivery confirmed manually by owner after checking the inbox.'
        notice.body='';notice.html_body=''
    else:raise ValidationError('This action is unavailable. Uncertain sends are never automatically retried.')
    notice.save(update_fields=['state','sent_at','last_error','body','html_body'])
    NativeBillingMailEvent.objects.create(delivery=delivery,actor=actor,action={'retry':'Failed receipt email retry queued','cancel':'Receipt email canceled','confirm_sent':'Receipt delivery confirmed by owner'}[action],details={'previous_state':expected_state,'attempts':attempts})
    return delivery
