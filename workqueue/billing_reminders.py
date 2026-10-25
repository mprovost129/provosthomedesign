"""Future-only reminder scheduling; never changes accounting or public access."""
import copy
import uuid
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q,Case,When,F,Sum,Value,DecimalField,Exists,OuterRef
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from .billing_mail import require_owner,ReceiptDocumentError
from .billing_reminder_models import NativeReminderSettings,NativeReminderPolicy,NativeInvoiceReminder,NativeReminderEvent
from .invoice_delivery import owner_test_address
from .invoice_delivery_models import NativeInvoiceDelivery
from .models import NotificationDelivery
from .native_invoice_models import NativeInvoice,NativeInvoiceEntry
from .native_invoices import balance
from .test_payment_models import NativePaymentAttempt,RESERVED_STATES
from .test_refunds import refund_hold
from .services import lock_queue,EditConflict

PREFIX='test-invoice-reminder-'
STAGES=(1,7,14)
ZERO=Decimal('0.00')


def settings_record():
    return NativeReminderSettings.objects.filter(pk=1).first() or NativeReminderSettings(pk=1)


def policy_record(invoice):
    return NativeReminderPolicy.objects.filter(invoice=invoice).first() or NativeReminderPolicy(invoice=invoice)


def event(action,*,actor=None,invoice=None,reminder=None,**details):
    return NativeReminderEvent.objects.create(action=action,actor=actor,invoice=invoice,reminder=reminder,details=details)


def cancel_notice(notice,reason):
    notice.state='canceled';notice.body='';notice.html_body='';notice.last_error=reason
    notice.save(update_fields=['state','body','html_body','last_error'])


def cancel_waiting(query,reason):
    for reminder in query:
        notice=NotificationDelivery.objects.select_for_update().get(pk=reminder.notification_id)
        if notice.state=='pending':cancel_notice(notice,reason)


@transaction.atomic
def save_settings(*,actor,version,enabled):
    require_owner(actor);lock_queue();config=settings_record()
    if config.version!=version:raise EditConflict('Reminder settings changed. Reload before saving.')
    if type(enabled) is not bool:raise ValidationError('Choose whether automatic reminders are enabled.')
    if config.enabled!=enabled:config.enabled_since=timezone.now() if enabled else None
    config.enabled=enabled;config.version+=1;config.changed_at=timezone.now();config.changed_by=actor
    config.full_clean();config.save()
    if not enabled:cancel_waiting(NativeInvoiceReminder.objects.filter(automatic=True,notification__state='pending'),'Automatic reminders were paused.')
    event('Automatic test reminders enabled' if enabled else 'Automatic test reminders paused',actor=actor,version=config.version)
    return config


@transaction.atomic
def save_policy(*,invoice_id,actor,version,paused,snooze_until):
    require_owner(actor);lock_queue();invoice=NativeInvoice.objects.get(pk=invoice_id);policy=policy_record(invoice)
    if policy.version!=version:raise EditConflict('This invoice reminder setting changed. Reload before saving.')
    if type(paused) is not bool:raise ValidationError('Choose whether reminders are paused.')
    if snooze_until and not timezone.localdate()<snooze_until<=timezone.localdate()+timedelta(days=366):
        raise ValidationError('Choose a snooze date after today and within the next year.')
    policy.paused=paused;policy.snooze_until=snooze_until;policy.version+=1;policy.changed_at=timezone.now();policy.changed_by=actor
    policy.full_clean();policy.save()
    if paused or snooze_until:cancel_waiting(invoice.reminders.filter(notification__state='pending'),'Invoice reminders were paused or snoozed.')
    event('Invoice follow-up settings saved',actor=actor,invoice=invoice,paused=paused,snooze_until=snooze_until.isoformat() if snooze_until else '')
    return policy


def invoice_query():
    return NativeInvoice.objects.select_related('plan__document__client').prefetch_related('entries')


def open_query():
    # Signed ledger activity, including reversals, refunds and recoveries. Correlated
    # aggregation avoids join multiplication when searching related reminder rows.
    field=DecimalField(max_digits=16,decimal_places=2)
    signed=Case(When(kind__in=['payment','credit','recovery'],then=-F('amount')),default=F('amount'),output_field=field)
    totals=NativeInvoiceEntry.objects.filter(invoice_id=OuterRef('pk')).values('invoice_id').annotate(total=Sum(signed)).values('total')
    return invoice_query().filter(voided_at__isnull=True,due_date__lt=timezone.localdate()).annotate(
        current_due=F('amount')+Coalesce(totals,Value(ZERO),output_field=field)).filter(current_due__gt=0)


def stage_for(invoice):
    days=(timezone.localdate()-invoice.due_date).days
    return next((stage for stage in reversed(STAGES) if days>=stage),None)


def reviewed_copy(invoice):
    return NativeInvoiceDelivery.objects.select_related('notification').filter(invoice=invoice,revoked_at__isnull=True,
        notification__state='sent',owner_recipient__iexact=owner_test_address()).order_by('-created_at','-pk').first()


def blocked_reason(invoice,delivery=None):
    if not invoice.number.startswith('TEST-INV-'):return 'Only sandbox test invoices can use this follow-up workspace.'
    if invoice.voided_at or balance(invoice)['due']<=0:return 'This invoice has no outstanding test balance.'
    if not stage_for(invoice):return 'This invoice is not overdue yet.'
    policy=policy_record(invoice)
    if policy.paused:return 'Reminders are paused for this invoice.'
    if policy.snooze_until and policy.snooze_until>timezone.localdate():return 'Reminders are snoozed for this invoice.'
    if NativePaymentAttempt.objects.filter(invoice=invoice).filter(Q(state__in=RESERVED_STATES)|~Q(issue_code='')).exists() or refund_hold(invoice):
        return 'A payment, refund or bank adjustment needs to settle or be reviewed first.'
    delivery=delivery or reviewed_copy(invoice)
    if not delivery or delivery.invoice_id!=invoice.pk or delivery.revoked_at or delivery.notification.state!='sent' or delivery.owner_recipient.lower()!=owner_test_address():
        return 'Send and verify a reviewed owner-only invoice copy before sending reminders.'
    return ''


def snapshot_for(invoice):
    amounts=balance(invoice)
    return {'number':invoice.number,'client_name':invoice.snapshot.get('client_name',''),
        'project_name':invoice.snapshot.get('project_name',''),'invoice_amount':str(invoice.amount),
        'due':str(amounts['due']),'paid':str(amounts['paid']),'credited':str(amounts['credited']),
        'due_date':invoice.due_date.isoformat(),'version':invoice.version,'stage':stage_for(invoice),
        'days_overdue':(timezone.localdate()-invoice.due_date).days,'test_only':True,
        'document':copy.deepcopy(invoice.snapshot)}


def review_state(invoice):
    stage=stage_for(invoice)
    existing=NativeInvoiceReminder.objects.select_related('notification').filter(invoice=invoice,stage=stage).first() if stage else None
    reason=blocked_reason(invoice)
    if not reason and invoice.reminders.filter(closed_unknown=True).exists():
        reason='A previous reminder has an uncertain delivery outcome. Check the inbox and resolve that reminder first.'
    if not reason and invoice.reminders.exclude(pk=existing.pk if existing else None).filter(notification__state__in=['pending','sending','unknown']).exists():
        reason='Resolve the previous queued or uncertain reminder first.'
    if existing and not reason:
        if existing.closed_unknown:reason='This stage was closed after an uncertain send. It cannot be sent again.'
        elif existing.notification.state not in {'failed','canceled'}:reason='This reminder stage is already queued, sent or awaiting delivery review.'
    return existing,reason


def message_context(reminder):
    from .notifications import site_url
    return {'reminder':reminder.snapshot,'target':reminder.owner_recipient,
        'staff_link':site_url(reverse('workqueue:native_invoice',args=[reminder.invoice_id]))}


def render_notice(reminder,notice):
    context=message_context(reminder)
    notice.subject=f'[TEST ONLY] Overdue reminder - {reminder.snapshot["number"]}'
    notice.body=render_to_string('workqueue/invoice_reminder_email.txt',context)
    notice.html_body=render_to_string('workqueue/invoice_reminder_email.html',context)
    notice.state='pending';notice.last_error='';notice.sent_at=None
    notice.recipient=reminder.owner_recipient;notice.recipient_kind='owner'
    notice.to_recipients=[];notice.cc_recipients=[];notice.bcc_recipients=[]
    notice.save(update_fields=['subject','body','html_body','state','last_error','sent_at','recipient','recipient_kind','to_recipients','cc_recipients','bcc_recipients'])


@transaction.atomic
def queue_reminder(*,invoice_id,actor=None,automatic=False,review=None):
    if type(automatic) is not bool:raise ValidationError('Choose a valid reminder mode.')
    if not automatic:require_owner(actor)
    if not automatic:
        try:uuid.UUID(review['token'])
        except (ValueError,TypeError,KeyError,AttributeError):raise ValidationError('Start a fresh reminder review.') from None
    lock_queue();invoice=invoice_query().get(pk=invoice_id);stage=stage_for(invoice)
    existing=NativeInvoiceReminder.objects.select_related('notification').filter(invoice=invoice,stage=stage).first() if stage else None
    if review and existing and str(existing.review_token)==review.get('token') and existing.actor_id==actor.pk:return existing
    delivery=reviewed_copy(invoice);reason=blocked_reason(invoice,delivery)
    if reason:raise ValidationError(reason)
    config=settings_record()
    if automatic:
        if (existing or not config.enabled or not config.enabled_since or invoice.due_date<timezone.localdate(config.enabled_since)):return None
        cutoff=timezone.now()-timedelta(hours=48)
        if invoice.reminders.filter(Q(updated_at__gt=cutoff)|Q(notification__sent_at__gt=cutoff)|Q(notification__last_attempt_at__gt=cutoff)).exists():return None
    current=snapshot_for(invoice);target=owner_test_address()
    if not automatic:
        expected={'invoice':str(invoice.pk),'actor':str(actor.pk),'owner':target,'version':invoice.version,
            'due':current['due'],'stage':stage,'delivery':str(delivery.pk),'existing':str(existing.pk) if existing else '',
            'state':existing.notification.state if existing else '', 'attempts':existing.notification.attempts if existing else 0}
        if not review or any(review.get(key)!=value for key,value in expected.items()):raise EditConflict('The invoice, recipient or reminder changed. Start a fresh review.')
        if existing and (existing.closed_unknown or existing.notification.state not in {'failed','canceled'}):
            raise ValidationError('This reminder is already queued, sent or awaiting review. No duplicate was sent.')
        # An unresolved older stage must be resolved before another stage is sent.
    if invoice.reminders.exclude(pk=existing.pk if existing else None).filter(notification__state__in=['pending','sending','unknown']).exists():
        raise ValidationError('Resolve the previous queued or uncertain reminder first.')
    if invoice.reminders.filter(closed_unknown=True).exists():
        raise ValidationError('A previous reminder has an unresolved delivery outcome. Automatic follow-up is held; check the inbox.')
    if existing:
        notice=NotificationDelivery.objects.select_for_update().get(pk=existing.notification_id)
        if existing.owner_recipient!=target:raise ValidationError('The original owner inbox changed. This reminder cannot be redirected.')
        previous=existing.snapshot
        existing.snapshot=current;existing.original_delivery=delivery;existing.automatic=False;existing.actor=actor
        existing.review_token=uuid.UUID(review['token']);existing.save()
        event('Known-unsent reminder reviewed again',actor=actor,invoice=invoice,reminder=existing,previous_due=previous['due'])
        reminder=existing
    else:
        notice=NotificationDelivery.objects.create(recipient_kind='owner',recipient=target,subject='',body='',provider_reference=PREFIX+uuid.uuid4().hex)
        reminder=NativeInvoiceReminder.objects.create(invoice=invoice,stage=stage,original_delivery=delivery,notification=notice,
            snapshot=current,owner_recipient=target,automatic=automatic,actor=actor,review_token=uuid.UUID(review['token']) if review else uuid.uuid4())
    render_notice(reminder,notice)
    reminder.notification=notice
    event('Sandbox overdue reminder queued',actor=actor,invoice=invoice,reminder=reminder,automatic=automatic,stage=stage,due=current['due'])
    return reminder


def queue_due_invoice_reminders(limit=100):
    config=settings_record()
    if not config.enabled or not config.enabled_since:return 0
    try:owner=owner_test_address()
    except ValidationError:return 0
    count=0;today=timezone.localdate();cap=max(1,min(limit,100))
    for stage in reversed(STAGES):
        candidates=open_query().filter(due_date__gte=timezone.localdate(config.enabled_since),due_date__lte=today-timedelta(days=stage))
        if stage!=14:candidates=candidates.filter(due_date__gt=today-timedelta(days=7 if stage==1 else 14))
        slot=NativeInvoiceReminder.objects.filter(invoice_id=OuterRef('pk'),stage=stage)
        holds=NativePaymentAttempt.objects.filter(invoice_id=OuterRef('pk')).filter(Q(state__in=RESERVED_STATES)|~Q(issue_code=''))
        candidates=candidates.annotate(slot_exists=Exists(slot),payment_hold=Exists(holds)).filter(slot_exists=False,payment_hold=False)
        candidates=candidates.exclude(reminder_policy__paused=True).exclude(reminder_policy__snooze_until__gt=today)
        candidates=candidates.exclude(reminders__closed_unknown=True).exclude(reminders__notification__state__in=['pending','sending','unknown'])
        cutoff=timezone.now()-timedelta(hours=48)
        recent=NativeInvoiceReminder.objects.filter(invoice_id=OuterRef('pk')).filter(
            Q(updated_at__gt=cutoff)|Q(notification__sent_at__gt=cutoff)|Q(notification__last_attempt_at__gt=cutoff))
        candidates=candidates.annotate(recent_followup=Exists(recent)).filter(recent_followup=False)
        candidates=candidates.filter(deliveries__revoked_at__isnull=True,deliveries__notification__state='sent',
            deliveries__owner_recipient__iexact=owner).distinct().order_by('due_date','pk')
        for invoice in candidates[:cap-count]:
            try:
                if queue_reminder(invoice_id=invoice.pk,automatic=True):count+=1
            except ValidationError:continue
        if count>=cap:break
    return count


def cancel_ineligible_invoice_reminder(notice):
    # Worker has the outbox lock; never acquire QueueState in this order.
    if not notice.provider_reference.startswith(PREFIX):return False
    reminder=NativeInvoiceReminder.objects.select_related('invoice','original_delivery__notification').filter(notification=notice).first()
    try:
        valid=bool(reminder and reminder.snapshot.get('test_only') is True and not reminder.closed_unknown
            and notice.recipient.lower()==reminder.owner_recipient.lower()==owner_test_address()
            and notice.recipient_kind=='owner' and not notice.to_recipients and not notice.cc_recipients and not notice.bcc_recipients)
        if valid:
            invoice=reminder.invoice
            valid=(not blocked_reason(invoice,reminder.original_delivery) and invoice.version==reminder.snapshot['version']
                and str(balance(invoice)['due'])==reminder.snapshot['due'] and stage_for(invoice)==reminder.stage)
        if valid and reminder.automatic:
            config=settings_record()
            valid=bool(config.enabled and config.enabled_since and reminder.created_at>=config.enabled_since
                and invoice.due_date>=timezone.localdate(config.enabled_since))
    except (ValidationError,KeyError):valid=False
    if valid:return False
    cancel_notice(notice,'Reminder canceled: the balance, payment status, owner inbox, authorization or follow-up settings changed. Review before trying again.')
    return True


def reminder_attachment(notice):
    if not notice.provider_reference.startswith(PREFIX):return None
    try:
        from .document_pdf import document_pdf
        from .native_invoice_document import test_invoice_snapshot
        reminder=NativeInvoiceReminder.objects.select_related('invoice').get(notification=notice)
        # Render the reviewed frozen invoice, not any staff-only ledger note.
        frozen_invoice=copy.copy(reminder.invoice);frozen_invoice.snapshot=reminder.snapshot['document']
        document=test_invoice_snapshot(frozen_invoice)
        return (reminder.invoice.number+'-test-only.pdf',document_pdf(document,test_invoice=True),'application/pdf')
    except Exception:raise ReceiptDocumentError('The reminder invoice PDF could not be prepared; no email was sent.') from None


@transaction.atomic
def resolve_reminder(*,reminder_id,actor,action,state,attempts):
    require_owner(actor);lock_queue();reminder=NativeInvoiceReminder.objects.get(pk=reminder_id)
    notice=NotificationDelivery.objects.select_for_update().get(pk=reminder.notification_id)
    if notice.state!=state or notice.attempts!=attempts:raise EditConflict('This reminder changed. Reload before choosing an action.')
    if action=='cancel' and state in {'pending','failed','unknown'}:
        if state=='unknown':reminder.closed_unknown=True;reminder.save(update_fields=['closed_unknown'])
        cancel_notice(notice,'Closed after owner review. An uncertain send cannot be retried.')
    elif action=='confirm_sent' and (state=='unknown' or reminder.closed_unknown):
        notice.state='sent';notice.sent_at=timezone.now();notice.body='';notice.html_body='';notice.last_error='Owner confirmed receipt after checking the inbox.'
        notice.save(update_fields=['state','sent_at','body','html_body','last_error'])
        reminder.closed_unknown=False;reminder.save(update_fields=['closed_unknown'])
    else:raise ValidationError('Choose an available action. Uncertain sends cannot be retried.')
    event('Reminder delivery resolved',actor=actor,invoice=reminder.invoice,reminder=reminder,action_taken=action,previous_state=state)
    return reminder
