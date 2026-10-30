from datetime import datetime, timedelta
from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .billing_mail import require_owner
from .freshbooks_mirror import selected
from .freshbooks_mirror_data import signature
from .live_reconciliation import MAX_BYTES, amount, month_start, import_export, create_check
from .live_reconciliation_models import FreshBooksPaymentExport
from .pm_views import staff_view, editable, error_text
from .test_payment_views import private


class ExportForm(forms.Form):
    month = forms.CharField(label='Payment month', widget=forms.TextInput(attrs={'type': 'month'}))
    file = forms.FileField(label='FreshBooks Online Payments transaction CSV')
    confirm = forms.BooleanField(label='This is the selected business’s complete Online Payments export for this month, with all statuses and no gateway or payment-method filters.')
    def clean_month(self):
        value = self.cleaned_data['month']; month_start(value); return value
    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > MAX_BYTES:
            raise ValidationError('The CSV must be 2 MB or smaller.')
        return file


def formatted(result):
    # Integer cents stay authoritative. Formatting never participates in math.
    result = {**result, 'rows': [dict(r) for r in result['rows']],
              'totals': [dict(r) for r in result['totals']], 'other_payments': [dict(r) for r in result['other_payments']]}
    for row in result['rows'] + result['totals'] + result['other_payments']:
        for key in ('gross', 'fee', 'known_fees', 'net'):
            if key in row:
                row[key + '_display'] = amount(row[key]) if row[key] is not None else None
    result['invoice_checked_at'] = datetime.fromisoformat(result['invoice_scan']['complete_at'])
    result['payments_checked_at'] = datetime.fromisoformat(result['payment_scan']['complete_at'])
    return result


@staff_view
@require_http_methods(['GET', 'POST'])
def center(request):
    require_owner(request.user)
    connection = selected()
    default_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    form = ExportForm(initial={'month': default_month}); error = ''; status = 200
    if request.method == 'POST':
        editable(request); form = ExportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                file = form.cleaned_data['file']
                evidence = import_export(actor=request.user, content=file.read(MAX_BYTES + 1), filename=file.name, month=form.cleaned_data['month'])
            except ValidationError as exc:
                error = error_text(exc); status = 400
            else:
                return redirect('workqueue:live_reconciliation_detail', evidence.pk)
        else:
            status = 400
    rows = FreshBooksPaymentExport.objects.filter(account_id=connection.account_id, business_id=connection.business_id)[:30] if connection else []
    return private(render(request, 'workqueue/live_reconciliation.html', {'connection': connection, 'form': form,
        'evidence_rows': rows, 'error': error}, status=status))


@staff_view
@require_http_methods(['GET', 'POST'])
def detail(request, evidence_id):
    require_owner(request.user); connection = selected()
    if not connection:
        return private(HttpResponse('Select your FreshBooks business first.', status=409))
    evidence = get_object_or_404(FreshBooksPaymentExport, pk=evidence_id, account_id=connection.account_id, business_id=connection.business_id)
    if signature(evidence.source) != evidence.source_hash:
        return private(HttpResponse('Saved export integrity needs review. Financial totals are unavailable.', status=409))
    error = ''; status = 200
    if request.method == 'POST':
        editable(request)
        if request.POST.get('action') != 'compare':
            return private(HttpResponse('Choose Compare with saved FreshBooks records.', status=400))
        try:
            check = create_check(actor=request.user, evidence_id=evidence.pk)
        except ValidationError as exc:
            error = error_text(exc); status = 409
        else:
            return redirect(f'{request.path}?check={check.pk}')
    check = None
    if request.GET.get('check'):
        try:
            identifier = forms.UUIDField().clean(request.GET['check'])
        except ValidationError:
            return private(HttpResponse('Choose a valid saved comparison.', status=400))
        check = get_object_or_404(evidence.checks, pk=identifier)
    else:
        check = evidence.checks.first()
    if check and (signature(check.result) != check.result_hash or check.result.get('evidence_hash') != evidence.source_hash):
        return private(HttpResponse('Saved comparison integrity needs review. Financial totals are unavailable.', status=409))
    return private(render(request, 'workqueue/live_reconciliation_detail.html', {'evidence': evidence,
        'check': check, 'result': formatted(check.result) if check else None, 'history': evidence.checks.all()[:20],
        'preview_rows': evidence.source['rows'], 'error': error}, status=status))
