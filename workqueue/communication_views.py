import uuid

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .crm_csv import apply_import, build_plan, export_csv, parse_csv
from .crm_mail import ComposeForm, PreferencesForm, compose_email, save_preferences
from .models import Client, ClientEmail, ClientImport, CRMSettings, WorkItem
from .services import EditConflict
from .views import queue_enabled, staff_preview


def editable(request):
    if staff_preview(request):
        raise PermissionDenied


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET', 'POST'])
@xframe_options_sameorigin
def compose(request, client_id):
    editable(request)
    client = get_object_or_404(Client, pk=client_id, merged_into__isnull=True)
    item_id = request.GET.get('item', '')
    item = None
    if item_id:
        try:
            item = get_object_or_404(WorkItem, pk=uuid.UUID(item_id), client_contact__client=client)
        except ValueError:
            return HttpResponse('Invalid work item.', status=400)
    initial = {'token': uuid.uuid4(), 'include_signature': True}
    contact = item.client_contact if item else client.primary_contact
    if contact and contact.email and not contact.archived_at:
        initial['contacts'] = [contact.pk]
    if item:
        initial['project'] = item.project
        initial['subject'] = (item.project.name if item.project else
            item.project_name or item.project_context or item.project_street or item.reference)[:255]
        initial['project_subject'] = f'project:{item.project_id}' if item.project_id else f'work:{item.pk}'
    form = ComposeForm(request.POST or None, client=client, initial=initial)
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                message, created = compose_email(client_id=client.pk, item_id=item.pk if item else None,
                                                data=form.cleaned_data, actor=request.user)
            except (ValidationError, EditConflict) as exc:
                form.add_error(None, exc)
            else:
                return redirect('workqueue:email_detail', message.pk)
    preferences = CRMSettings.objects.filter(pk=1).first()
    from .rich_email import clean_email_html
    signature_html, signature_text = clean_email_html(preferences.signature_html if preferences else '')
    return render(request, 'workqueue/compose_email.html', {'form': form, 'crm_client': client,
        'signature_html': signature_html if signature_text else '',
        'has_recipients': form.fields['contacts'].queryset.exists()}, status=status)


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required('workqueue.view_workitem', raise_exception=True)
@never_cache
@require_http_methods(['GET'])
@xframe_options_sameorigin
def email_detail(request, message_id):
    message = get_object_or_404(ClientEmail.objects.select_related('delivery', 'client', 'project', 'actor'), pk=message_id)
    return render(request, 'workqueue/email_detail.html', {'email': message})


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET', 'POST'])
def communication_settings(request):
    editable(request)
    prefs = CRMSettings.objects.filter(pk=1).first() or CRMSettings(pk=1)
    form = PreferencesForm(request.POST or None, instance=prefs, initial={'version': prefs.version})
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                save_preferences(form.cleaned_data, request.user)
            except EditConflict as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Email signature and morning reminder settings saved.')
                return redirect('workqueue:communication_settings')
    return render(request, 'workqueue/communication_settings.html', {'form': form,
        'owner_email': settings.INTAKE_OWNER_EMAIL}, status=status)


class ImportForm(forms.Form):
    file = forms.FileField(label='Client CSV')


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET', 'POST'])
def import_clients(request):
    editable(request)
    form = ImportForm(request.POST or None, request.FILES or None)
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                rows = parse_csv(form.cleaned_data['file'])
                plan = build_plan(rows)
            except ValidationError as exc:
                form.add_error('file', exc)
            else:
                batch = ClientImport.objects.create(actor=request.user, rows=rows, plan=plan)
                return redirect('workqueue:import_preview', batch.pk)
    return render(request, 'workqueue/client_import.html', {'form': form}, status=status)


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET', 'POST'])
def import_preview(request, batch_id):
    editable(request)
    batch = get_object_or_404(ClientImport, pk=batch_id, actor=request.user)
    error, status = '', 200
    if request.method == 'POST' and not batch.applied_at:
        if request.POST.get('confirmed') != 'on':
            error, status = 'Confirm the reviewed import before applying it.', 400
        else:
            choices = {str(index): request.POST.get(f'choice_{index}', '') for index in range(len(batch.rows))}
            try:
                result = apply_import(batch.pk, choices, request.user)
            except (ValidationError, EditConflict) as exc:
                error, status = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc), 400
            else:
                return redirect('workqueue:import_preview', batch.pk)
    entries = []
    for index, (row, plan) in enumerate(zip(batch.rows, batch.plan)):
        entries.append({'row': row, 'plan': plan, 'selected': request.POST.get(f'choice_{index}', '')})
    return render(request, 'workqueue/client_import_preview.html', {'batch': batch, 'entries': entries,
        'error': error, 'row_count': len(entries)}, status=status)


@queue_enabled
@staff_member_required(login_url='workqueue:staff_login')
@permission_required(('workqueue.view_workitem', 'workqueue.change_workitem'), raise_exception=True)
@never_cache
@require_http_methods(['GET'])
def export_clients(request):
    editable(request)
    template = request.GET.get('template') == '1'
    response = HttpResponse(export_csv(template), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="clients-{"template" if template else "export"}.csv"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
