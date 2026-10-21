from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .operations import queue_test_alert
from .operations_models import OperationalIncident, OperationsSettings
from .pm_views import staff_view, editable


@staff_view
@require_http_methods(['GET','POST'])
def operations(request):
    if request.method == 'POST':
        editable(request)
        try:
            action=request.POST.get('action')
            if action == 'test':
                queue_test_alert()
                messages.success(request,'Test alert queued for the owner email. Check delivery below in a minute.')
            elif action == 'preferences':
                with transaction.atomic():
                    OperationsSettings.objects.get_or_create(pk=1)
                    state=OperationsSettings.objects.select_for_update().get(pk=1)
                    version=forms.IntegerField(min_value=1).clean(request.POST.get('version'))
                    if version != state.version:
                        raise ValidationError('Settings changed in another tab. Refresh and try again.')
                    state.email_alerts=request.POST.get('email_alerts') == 'on'
                    state.version+=1
                    state.save(update_fields=['email_alerts','version'])
                messages.success(request,'Error alert preferences saved.')
            elif action == 'review':
                key=forms.UUIDField().clean(request.POST.get('incident'))
                count=forms.IntegerField(min_value=1).clean(request.POST.get('occurrences'))
                with transaction.atomic():
                    incident=get_object_or_404(OperationalIncident.objects.select_for_update(),pk=key)
                    if incident.occurrences != count:
                        raise ValidationError('More occurrences were recorded. Review the updated count before marking reviewed.')
                    from django.utils import timezone
                    incident.reviewed_count=incident.occurrences
                    incident.reviewed_at=timezone.now()
                    incident.save(update_fields=['reviewed_count','reviewed_at'])
                messages.success(request,'Marked reviewed. A new occurrence will return to the list.')
            else:
                raise ValidationError('Choose an available action.')
        except ValidationError as exc:
            messages.error(request,' '.join(exc.messages))
        return redirect('workqueue:operations')
    scope=request.GET.get('scope','unreviewed')
    if scope not in {'all','unreviewed'}:
        scope='unreviewed'
    q=request.GET.get('q','').strip()[:200]
    incidents=OperationalIncident.objects.all()
    pending=incidents.filter(occurrences__gt=F('reviewed_count')).count()
    if scope == 'unreviewed':
        incidents=incidents.filter(occurrences__gt=F('reviewed_count'))
    if q:
        incidents=incidents.filter(Q(reference__icontains=q)|Q(area__icontains=q)|Q(error_type__icontains=q))
    state=OperationsSettings.objects.select_related('last_delivery','last_test_delivery').filter(pk=1).first() or OperationsSettings()
    return render(request,'workqueue/operations.html',{'operations_state':state,'unreviewed_count':pending,
        'page':Paginator(incidents.order_by('-last_seen','-pk'),25).get_page(request.GET.get('page')),
        'scope':scope,'q':q,'can_change':request.user.has_perm('workqueue.change_workitem')})
