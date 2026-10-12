from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .pm_views import staff_view, editable, error_text, query_uuid
from .models import Client, WorkProject
from .freshbooks_models import FreshBooksConnection
from .invoice_history_models import HistoricalInvoice, InvoiceCSVImport, InvoiceHistoryEvent
from .invoice_csv import parse_export, preview_plan, digest, apply_export, change_batch_visibility
from .services import EditConflict, lock_queue
from .views import staff_preview

SALT = 'workqueue.invoice-import.v1'


class UploadForm(forms.Form):
    file = forms.FileField(label='FreshBooks invoice CSV')


@staff_view
@require_http_methods(['GET', 'POST'])
def import_invoices(request):
    editable(request)
    form = UploadForm(request.POST or None, request.FILES or None)
    connection = FreshBooksConnection.objects.filter(pk=1).first()
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                if not connection or not connection.account_id:
                    raise ValidationError('Select the existing FreshBooks business before importing its invoice history.')
                invoices, source_hash = parse_export(form.cleaned_data['file'])
            except ValidationError as exc:
                form.add_error('file', exc)
            else:
                batch = InvoiceCSVImport.objects.create(actor=request.user, account_id=connection.account_id,
                    business_name=connection.business_name, filename=form.cleaned_data['file'].name[:255],
                    source_hash=source_hash, invoices=invoices)
                return redirect('workqueue:invoice_import_preview', batch.pk)
    return render(request, 'workqueue/invoice_import.html', {'form': form, 'connection': connection}, status=status)


@staff_view
@require_http_methods(['GET', 'POST'])
def import_preview(request, batch_id):
    editable(request)
    batch = get_object_or_404(InvoiceCSVImport, pk=batch_id, actor=request.user)
    error, status = '', 200
    if request.method == 'POST':
        try:
            if request.POST.get('confirmed') != 'on':
                raise ValidationError('Confirm the reviewed import action below.')
            action = request.POST.get('action', 'apply')
            if action in ('remove', 'restore'):
                change_batch_visibility(batch.pk, request.user, action == 'remove')
                messages.success(request, 'Imported records hidden. You can restore this batch.' if action == 'remove' else 'Imported records restored.')
            elif action == 'apply':
                try:
                    signed = signing.loads(request.POST.get('signature', ''), salt=SALT, max_age=3600)
                except signing.BadSignature:
                    raise ValidationError('This preview expired. Review the refreshed preview below.') from None
                if signed.get('batch') != str(batch.pk) or signed.get('actor') != request.user.pk:
                    raise ValidationError('Use the preview belonging to this signed-in account.')
                choices = {str(i): request.POST.get(f'choice_{i}', '') for i in range(len(signed.get('names', [])))}
                apply_export(batch.pk, signed['plan'], choices, request.user)
            else:
                raise ValidationError('Choose an import action.')
            return redirect('workqueue:invoice_import_preview', batch.pk)
        except (ValidationError, EditConflict) as exc:
            error, status = error_text(exc), 400
    plan = preview_plan(batch) if not batch.applied_at else {'mappings': [], 'invoices': []}
    for index, mapping in enumerate(plan['mappings']):
        mapping['index'] = index
        mapping['selected'] = request.POST.get(f'choice_{index}', '') if request.method == 'POST' else mapping['recommended']
    # UI-only selected/index are excluded from the signed service plan.
    service_plan = preview_plan(batch) if not batch.applied_at else plan
    signature = signing.dumps({'batch': str(batch.pk), 'actor': request.user.pk, 'plan': digest(service_plan),
        'names': [m['name'] for m in service_plan['mappings']]}, salt=SALT)
    summary = {key: sum(i['action'] == key for i in plan['invoices']) for key in ('import', 'linked', 'duplicate', 'conflict', 'removed')}
    return render(request, 'workqueue/invoice_import_preview.html', {'batch': batch, 'plan': plan, 'signature': signature,
        'summary': summary, 'invoice_count': len(batch.invoices), 'error': error}, status=status)


@staff_view
@require_GET
def history(request):
    q = request.GET.get('q', '').strip()[:200]
    state = request.GET.get('state', 'all')
    state = state if state in ('all', 'paid', 'unpaid', 'draft', 'unassigned') else 'all'
    client_filter = get_object_or_404(Client, pk=query_uuid(request.GET['client'])) if request.GET.get('client') else None
    project_filter = get_object_or_404(WorkProject, pk=query_uuid(request.GET['project'])) if request.GET.get('project') else None
    invoices = HistoricalInvoice.objects.filter(removed_at__isnull=True).select_related('client', 'project', 'batch').annotate(status_key=Lower('exported_status'))
    if client_filter:
        # Merged profiles preserve their original invoice associations. Follow all
        # descendant profiles for the kept client's history without rewriting facts.
        ids = {client_filter.pk}
        frontier = ids
        while frontier:
            frontier = set(Client.objects.filter(merged_into_id__in=frontier).values_list('pk', flat=True)) - ids
            ids |= frontier
        invoices = invoices.filter(client_id__in=ids)
    if project_filter:
        invoices = invoices.filter(project=project_filter)
    if q:
        invoices = invoices.filter(Q(number__icontains=q) | Q(exported_client__icontains=q) | Q(client__name__icontains=q) |
            Q(project__name__icontains=q) | Q(project__street__icontains=q) | Q(lines__icontains=q))
    if state == 'paid': invoices = invoices.filter(status_key__in=['paid', 'autopaid'])
    elif state == 'draft': invoices = invoices.filter(status_key__in=['draft', 'created'])
    elif state == 'unassigned': invoices = invoices.filter(client__isnull=True)
    elif state == 'unpaid': invoices = invoices.exclude(status_key__in=['paid', 'autopaid', 'draft', 'created', 'deleted', 'voided'])
    summary = invoices.aggregate(total=Count('pk'), paid_count=Count('pk', filter=Q(status_key__in=['paid', 'autopaid'])), unassigned=Count('pk', filter=Q(client__isnull=True)))
    totals = list(invoices.order_by().values('currency').annotate(exported_total=Sum('line_total')).order_by('currency'))
    page = Paginator(invoices.order_by('-issued', '-number', '-pk'), 30).get_page(request.GET.get('page'))
    return render(request, 'workqueue/invoice_history.html', {'page': page, 'summary': summary, 'totals': totals,
        'q': q, 'state': state, 'client_filter': client_filter, 'project_filter': project_filter,
        'can_change': request.user.has_perm('workqueue.change_workitem') and not staff_preview(request)})


class AssociationForm(forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.filter(merged_into__isnull=True), required=False, label='CRM client')
    project = forms.ModelChoiceField(queryset=WorkProject.objects.all().order_by('name'), required=False, label='Existing project')
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


@staff_view
@require_http_methods(['GET', 'POST'])
def history_detail(request, invoice_id):
    invoice = get_object_or_404(HistoricalInvoice.objects.select_related('client', 'project', 'batch'), pk=invoice_id)
    can_change = not staff_preview(request) and request.user.has_perm('workqueue.change_workitem') and not invoice.removed_at
    form = AssociationForm(request.POST or None, initial={'client': invoice.client_id, 'project': invoice.project_id, 'version': invoice.version})
    status = 200
    if request.method == 'POST':
        editable(request)
        status = 400
        if form.is_valid():
            try:
                with transaction.atomic():
                    lock_queue()
                    invoice = HistoricalInvoice.objects.select_for_update().get(pk=invoice_id)
                    if invoice.version != form.cleaned_data['version'] or invoice.removed_at:
                        raise EditConflict('This record changed. Reload before saving its associations.')
                    client = form.cleaned_data['client']
                    if client and Client.objects.filter(pk=client.pk, merged_into__isnull=False).exists():
                        raise EditConflict('This client was connected to another record. Reload before saving.')
                    before = {'client': str(invoice.client_id or ''), 'project': str(invoice.project_id or '')}
                    invoice.client, invoice.project = client, form.cleaned_data['project']
                    after = {'client': str(invoice.client_id or ''), 'project': str(invoice.project_id or '')}
                    if before != after:
                        invoice.version += 1
                        invoice.save(update_fields=['client', 'project', 'version'])
                        InvoiceHistoryEvent.objects.create(invoice=invoice, actor=request.user, changes={'before': before, 'after': after})
                messages.success(request, 'Invoice history associations saved. Exported invoice information is unchanged.')
                return redirect('workqueue:invoice_history_detail', invoice.pk)
            except (EditConflict, ValidationError) as exc:
                form.add_error(None, str(exc))
    return render(request, 'workqueue/invoice_history_detail.html', {'invoice': invoice, 'form': form,
        'line_cells': [[line.get(k, '') for k in ('Item Name', 'Item Description', 'Rate', 'Quantity',
            'Discount Percentage', 'Line Subtotal', 'Tax 1 Amount', 'Tax 2 Amount', 'Line Total')] for line in invoice.lines],
        'can_change': can_change, 'events': invoice.events.select_related('actor').order_by('-created_at', '-pk')[:20]}, status=status)
