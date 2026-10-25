import uuid
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q,Sum,Count
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_http_methods
from .billing_mail import require_owner
from .billing_reminders import (settings_record,policy_record,save_settings,save_policy,invoice_query,open_query,
    stage_for,review_state,reviewed_copy,snapshot_for,queue_reminder,resolve_reminder)
from .billing_reminder_models import NativeInvoiceReminder,NativeReminderEvent
from .invoice_delivery import owner_test_address
from .pm_views import staff_view,editable,error_text
from .services import EditConflict

SALT='workqueue.invoice-reminder-review.v1'


class SettingsForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    enabled=forms.BooleanField(required=False,label='Automatically send future sandbox overdue reminders to my owner inbox')


class PolicyForm(forms.Form):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    paused=forms.BooleanField(required=False,label='Pause all reminders for this invoice')
    snooze_until=forms.DateField(required=False,label='Or snooze until',widget=forms.DateInput(attrs={'type':'date'}),
        help_text='Reminders may resume on this date. Leave blank to remove a snooze. The latest eligible stage is used; missed reminders are not sent in a burst.')


class SendForm(forms.Form):
    review=forms.CharField(widget=forms.HiddenInput,max_length=10000)
    confirm=forms.BooleanField(label='Send this TEST reminder and invoice PDF only to the owner inbox shown above. No client email or real payment request.')


class ResolveForm(forms.Form):
    reminder=forms.UUIDField(widget=forms.HiddenInput)
    state=forms.CharField(max_length=20,widget=forms.HiddenInput)
    attempts=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    resolution_action=forms.ChoiceField(choices=[('cancel','Cancel or close this reminder'),('confirm_sent','I checked my inbox and received this email')])
    confirm=forms.BooleanField(label='I reviewed this status. For an uncertain send, I checked the owner inbox first.')


def review_payload(invoice,actor,existing,target):
    delivery=reviewed_copy(invoice)
    return {'invoice':str(invoice.pk),'actor':str(actor.pk),'owner':target,'version':invoice.version,
        'due':snapshot_for(invoice)['due'],'stage':stage_for(invoice),'delivery':str(delivery.pk) if delivery else '',
        'existing':str(existing.pk) if existing else '', 'state':existing.notification.state if existing else '',
        'attempts':existing.notification.attempts if existing else 0,'token':str(uuid.uuid4())}


@staff_view
@require_http_methods(['GET','POST'])
def center(request):
    config=settings_record();status=200
    form=SettingsForm(request.POST if request.method=='POST' else None,initial={'version':config.version,'enabled':config.enabled})
    if request.method=='POST':
        editable(request);require_owner(request.user);status=400
        if form.is_valid():
            try:save_settings(actor=request.user,**form.cleaned_data)
            except (ValidationError,EditConflict) as exc:form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Test reminder settings saved. Existing overdue invoices are not backfilled.')
                return redirect('workqueue:billing_followups')
    q=request.GET.get('q','').strip()[:150]
    invoices=open_query().order_by('due_date','number')
    reminders=NativeInvoiceReminder.objects.select_related('invoice','notification','actor')
    if q:
        match=Q(number__icontains=q)|Q(snapshot__client_name__icontains=q)|Q(snapshot__project_name__icontains=q)
        invoices=invoices.filter(match)
        reminders=reminders.filter(Q(invoice__number__icontains=q)|Q(snapshot__client_name__icontains=q)|Q(snapshot__project_name__icontains=q))
    totals=invoices.aggregate(amount=Sum('current_due'),count=Count('pk'))
    page=Paginator(invoices,25).get_page(request.GET.get('page'))
    for invoice in page:
        try:existing,reason=review_state(invoice)
        except ValidationError:existing=None;reason='Configure the owner inbox before reviewing reminders.'
        invoice.followup_reason=reason;invoice.followup_stage=stage_for(invoice);invoice.followup_existing=existing
        invoice.followup_policy=policy_record(invoice)
        invoice.days_overdue=snapshot_for(invoice)['days_overdue']
    try:target=owner_test_address()
    except ValidationError:target='Owner inbox needs configuration'
    all_notices=NativeInvoiceReminder.objects
    counts={row['notification__state']:row['count'] for row in all_notices.values('notification__state').annotate(count=Count('pk'))}
    return render(request,'workqueue/billing_followups.html',{'form':form,'config':config,'q':q,'page':page,'totals':totals,'target':target,
        'history':Paginator(reminders,25).get_page(request.GET.get('history_page')),
        'waiting':counts.get('pending',0)+counts.get('sending',0),'attention':counts.get('failed',0)+counts.get('unknown',0)+all_notices.filter(closed_unknown=True).count(),
        'sent':counts.get('sent',0),'events':NativeReminderEvent.objects.select_related('invoice','actor')[:15]},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def invoice_followup(request,invoice_id):
    invoice=get_object_or_404(invoice_query(),pk=invoice_id);policy=policy_record(invoice);status=200;error=''
    action=request.POST.get('action','') if request.method=='POST' else ''
    policy_form=PolicyForm(request.POST if action=='policy' else None,prefix='policy',initial={'version':policy.version,'paused':policy.paused,'snooze_until':policy.snooze_until})
    try:
        existing,reason=review_state(invoice);target=owner_test_address()
        payload=review_payload(invoice,request.user,existing,target)
    except ValidationError:
        existing=None;reason='Configure the owner inbox before reviewing reminders.';target='';payload={}
    send_form=SendForm(request.POST if action=='send' else None,initial={'review':signing.dumps(payload,salt=SALT,compress=True)})
    resolve_form=ResolveForm(request.POST if action=='resolve' else None)
    if request.method=='POST':
        editable(request);require_owner(request.user);status=400
        try:
            if action=='policy' and policy_form.is_valid():save_policy(invoice_id=invoice.pk,actor=request.user,**policy_form.cleaned_data)
            elif action=='send' and send_form.is_valid():
                reviewed=signing.loads(send_form.cleaned_data['review'],salt=SALT,max_age=900)
                if reviewed.get('actor')!=str(request.user.pk) or reviewed.get('invoice')!=str(invoice.pk):raise ValidationError('The review belongs to another invoice or owner.')
                queue_reminder(invoice_id=invoice.pk,actor=request.user,review=reviewed)
            elif action=='resolve' and resolve_form.is_valid():
                data=resolve_form.cleaned_data
                reminder=get_object_or_404(NativeInvoiceReminder,pk=data['reminder'],invoice=invoice)
                resolve_reminder(reminder_id=reminder.pk,actor=request.user,action=data['resolution_action'],state=data['state'],attempts=data['attempts'])
            else:raise ValidationError('Review the highlighted fields and confirm the available action.')
        except (signing.BadSignature,ValidationError,EditConflict) as exc:
            error='This review expired. Start a fresh review.' if isinstance(exc,signing.BadSignature) else error_text(exc)
            status=409 if isinstance(exc,EditConflict) else 400
        else:
            messages.success(request,'Invoice follow-up saved. No client was emailed.')
            return redirect('workqueue:invoice_followup',invoice.pk)
    history=list(invoice.reminders.select_related('notification','actor'))
    for reminder in history:
        reminder.can_cancel=reminder.notification.state in {'pending','failed','unknown'}
        reminder.can_confirm=reminder.notification.state=='unknown' or reminder.closed_unknown
        reminder.resolve_initial=ResolveForm(initial={'reminder':reminder.pk,'state':reminder.notification.state,'attempts':reminder.notification.attempts})
    return render(request,'workqueue/invoice_followup.html',{'invoice':invoice,'snapshot':snapshot_for(invoice),'existing':existing,
        'reason':reason,'target':target,'policy_form':policy_form,'send_form':send_form,'resolve_form':resolve_form,'history':history,
        'error':error,'events':invoice.reminder_events.select_related('actor')[:20]},status=status)
