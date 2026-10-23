import uuid
from datetime import timedelta
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .pm_models import Proposal,ProposalRevision
from .pm_forms import LineFormSet
from .pm_views import staff_view,editable,error_text
from .document_views import editor_context
from .change_orders import ChangeOrderForm,active_base,amounts,form_data,save_change_order
from .services import EditConflict


@staff_view
@require_http_methods(['GET','POST'])
def editor(request,base_id,proposal_id=None):
    editable(request)
    base=get_object_or_404(ProposalRevision.objects.select_related('proposal__client','proposal__project'),pk=base_id)
    proposal=get_object_or_404(Proposal,pk=proposal_id,base_revision=base) if proposal_id else None
    if proposal and not proposal.has_draft:
        return redirect('workqueue:proposal_detail',proposal.pk)
    initial=dict(proposal.draft) if proposal else {'title':('Additional work · '+base.snapshot['title'])[:200],'document_kind':'change_order',
        'pricing':base.snapshot['pricing'],'scope':'','deliverables':'','schedule_affected':'no',
        'issue_date':timezone.localdate(),'valid_until':timezone.localdate()+timedelta(days=30)}
    from .pm_services import current_client
    initial.update(client=current_client(base.proposal.client),token=proposal.pk if proposal else uuid.uuid4(),version=proposal.version if proposal else 0)
    form=ChangeOrderForm(request.POST or None,project=base.proposal.project,proposal=base.proposal,initial=initial)
    # The agreement determines the billing client. A forged client cannot change it.
    form.fields['client'].disabled=True
    lines=LineFormSet(request.POST or None,prefix='lines',initial=initial.get('lines') or [{'description':'','quantity':'1','rate':''}])
    status=200;blocking_error=''
    try:active_base(base)
    except ValidationError as exc:
        blocking_error=error_text(exc);status=409
    if request.method=='POST' and status==200:
        status=400;valid=form.is_valid();valid_lines=lines.is_valid()
        if valid and valid_lines:
            try:
                proposal=save_change_order(base.pk,form_data(form,lines),form.cleaned_data['token'],form.cleaned_data['version'],request.user,proposal.pk if proposal else None)
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Change order draft saved. Review its price and schedule before sending.')
                return redirect('workqueue:proposal_detail',proposal.pk)
    context=editor_context(form,base.proposal.project)
    context.update(editor_kind='change_order',change_order_summary=amounts(base),change_order_base=base,
        change_order_fields=[form[name] for name in ('schedule_affected','additional_days','proposed_completion_date')],change_order_error=blocking_error)
    return render(request,'workqueue/document_editor.html',{'form':form,'lines':lines,'proposal':proposal,'project':base.proposal.project,**context},status=status)
