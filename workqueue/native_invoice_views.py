import uuid
from datetime import timedelta
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .document_models import InvoiceDocument
from .native_invoice_models import NativeBillingPlan, NativeInvoice
from .native_invoice_forms import PlanForm, ClosePlanForm, IssueForm, EntryForm, ReverseForm, VoidForm
from .native_invoices import create_plan, close_plan, issue_test_invoice, record_entry, reverse_entry, void_invoice, plan_summary, balance
from .pm_views import staff_view, editable, error_text, query_uuid
from .services import EditConflict


@staff_view
@require_http_methods(['GET'])
def overview(request):
    q=request.GET.get('q','').strip()[:150]
    invoices=NativeInvoice.objects.select_related('plan__document__client','plan__document__project').prefetch_related('entries')
    plans=NativeBillingPlan.objects.select_related('document__client','document__project').prefetch_related('invoices')
    if q:
        match=Q(snapshot__client_name__icontains=q)|Q(snapshot__project_name__icontains=q)|Q(snapshot__title__icontains=q)
        invoices=invoices.filter(match|Q(number__icontains=q))
        plans=plans.filter(match)
    page=Paginator(invoices,25).get_page(request.GET.get('page'))
    for invoice in page:
        invoice.balances=balance(invoice)
    return render(request,'workqueue/native_invoice_overview.html',{'q':q,'page':page,'plans':plans[:20]})


@staff_view
@require_http_methods(['GET','POST'])
def start_plan(request,document_id):
    editable(request)
    document=get_object_or_404(InvoiceDocument.objects.select_related('project','client'),pk=document_id)
    existing=NativeBillingPlan.objects.filter(document=document,closed_at__isnull=True).first()
    if existing and request.method=='GET':
        return redirect('workqueue:native_plan',existing.pk)
    form=PlanForm(request.POST or None,initial={'token':uuid.uuid4(),'version':document.version,'discount':0,'tax_percent':0})
    status=200
    if request.method=='POST':
        status=400
        if form.is_valid():
            try:
                plan=create_plan(document_id=document.pk,version=form.cleaned_data['version'],discount=form.cleaned_data['discount'],
                    tax_percent=form.cleaned_data['tax_percent'],token=form.cleaned_data['token'],actor=request.user)
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Test billing plan created. No client invoice was sent.')
                return redirect('workqueue:native_plan',plan.pk)
    return render(request,'workqueue/native_plan_start.html',{'form':form,'document':document,'snapshot':document.revisions.get(sequence=document.version).snapshot},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def plan_detail(request,plan_id):
    plan=get_object_or_404(NativeBillingPlan.objects.select_related('document__project','document__client','revision'),pk=plan_id)
    summary=plan_summary(plan)
    closing=request.method=='POST' and request.POST.get('action')=='close'
    close_form=ClosePlanForm(request.POST if closing else None,prefix='close',initial={'token':uuid.uuid4()})
    form=IssueForm(request.POST if request.method=='POST' and not closing else None,plan=plan,remaining=summary['remaining'],initial={'token':uuid.uuid4(),
        'stage':'final','description':plan.snapshot['title'][:175]+' - remaining work','basis':'remaining',
        'issue_date':timezone.localdate(),'due_date':timezone.localdate()+timedelta(days=30)})
    status=200
    if request.method=='POST':
        editable(request);status=400
        if closing and close_form.is_valid():
            try:
                close_plan(plan_id=plan.pk,actor=request.user,**{key:close_form.cleaned_data[key] for key in ['note','token']})
            except ValidationError as exc:
                close_form.add_error(None,error_text(exc))
            else:
                messages.success(request,'Test plan closed. You can now create a replacement from the working draft.')
                return redirect('workqueue:native_plan',plan.pk)
        if form.is_valid():
            try:
                data=form.cleaned_data
                invoice=issue_test_invoice(plan_id=plan.pk,actor=request.user,
                    **{key:data[key] for key in ['stage','amount','description','issue_date','due_date','token']})
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Test invoice created. It was not sent, charged or added to FreshBooks.')
                return redirect('workqueue:native_invoice',invoice.pk)
    invoices=list(plan.invoices.prefetch_related('entries'))
    for invoice in invoices:invoice.balances=balance(invoice)
    return render(request,'workqueue/native_plan.html',{'plan':plan,'summary':summary,'form':form,'close_form':close_form,'invoices':invoices,'snapshot':plan.snapshot},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def invoice_detail(request,invoice_id):
    invoice=get_object_or_404(NativeInvoice.objects.select_related('plan__document__client','plan__document__project').prefetch_related('entries__reverses'),pk=invoice_id)
    amounts=balance(invoice)
    entries=list(invoice.entries.all())
    active=[entry for entry in entries if entry.kind!='reversal' and entry.pk not in amounts['reversed_ids']]
    initial={'token':uuid.uuid4(),'version':invoice.version,'entry_date':timezone.localdate(),'kind':'payment'}
    action=request.POST.get('action') if request.method=='POST' else None
    forms={
        'entry':EntryForm(request.POST if request.method=='POST' and action not in {'reverse','void'} else None,prefix='entry',initial=initial),
        'reverse':ReverseForm(request.POST if action=='reverse' else None,prefix='reverse',initial={**initial,'token':uuid.uuid4()},entries=active),
        'void':VoidForm(request.POST if action=='void' else None,prefix='void',initial={**initial,'token':uuid.uuid4()})}
    status=200
    if request.method=='POST':
        editable(request);status=400
        if action not in forms:
            forms['entry'].is_valid()
            forms['entry'].add_error(None,'Choose an invoice action.')
        elif forms[action].is_valid():
            form=forms[action];data=form.cleaned_data
            try:
                common={'invoice_id':invoice.pk,'version':data['version'],'note':data['note'],'token':data['token'],'actor':request.user}
                if action=='entry':
                    record_entry(**common,**{key:data[key] for key in ['kind','amount','entry_date','method','reference']})
                elif action=='reverse':
                    reverse_entry(**common,entry_id=query_uuid(data['entry']))
                else:
                    void_invoice(**common)
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Test accounting record saved. No payment, refund or email was processed.')
                return redirect('workqueue:native_invoice',invoice.pk)
    return render(request,'workqueue/native_invoice.html',{'invoice':invoice,'plan':invoice.plan,'amounts':amounts,'entries':entries,
        'active_entries':active,'forms':forms,'snapshot':invoice.snapshot,'events':invoice.events.select_related('actor')[:100],
        'payment_attempts':invoice.payment_attempts.all()[:10],
        'payment_hold':invoice.payment_attempts.filter(state__in=['creating','open','processing','review']).exists(),
        'deliveries':invoice.deliveries.select_related('notification')[:20]},status=status)


@staff_view
@require_http_methods(['GET'])
def invoice_pdf(request,invoice_id):
    invoice=get_object_or_404(NativeInvoice,pk=invoice_id)
    from .document_pdf import document_pdf
    from .native_invoice_document import test_invoice_snapshot
    snapshot=test_invoice_snapshot(invoice)
    response=HttpResponse(document_pdf(snapshot,invoice_draft=True,test_invoice=True),content_type='application/pdf')
    response['Content-Disposition']=f'attachment; filename="{invoice.number}-test-only.pdf"'
    return response
