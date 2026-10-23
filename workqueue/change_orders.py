"""Additive scope agreements backed by the existing private version/response flow."""
import copy
from datetime import date
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from .crm import require_staff
from .pm_models import Proposal, ProposalRevision
from .pm_forms import ProposalForm, draft_data
from .pm_services import checked_draft, current_client, event
from .services import EditConflict, lock_queue


class ChangeOrderForm(ProposalForm):
    document_kind = forms.ChoiceField(choices=[('change_order', 'Change order')], initial='change_order', label='Document type')
    schedule_affected = forms.ChoiceField(choices=[('no','No schedule change'),('yes','Yes, the schedule changes')], label='Does this affect the schedule?', initial='no')
    additional_days = forms.IntegerField(required=False,min_value=0,max_value=3650,label='Additional calendar days (estimate)')
    proposed_completion_date = forms.DateField(required=False,label='Proposed completion date',widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['timeline'].label='Schedule impact / explanation'
        self.fields['timeline'].help_text='Required if the schedule changes. This does not automatically reschedule the queue.'

    def clean(self):
        data=super().clean()
        if data.get('schedule_affected')=='yes' and not data.get('timeline','').strip():
            self.add_error('timeline','Explain the schedule change for the client.')
        if data.get('schedule_affected')=='no':
            if data.get('additional_days') or data.get('proposed_completion_date'):
                self.add_error('schedule_affected','Choose a schedule change before adding days or a completion date.')
            data['timeline']='No change to the current schedule.'
            data['timeline_html']=''
        return data


def form_data(form,lines):
    data=draft_data(form,lines)
    data.update(schedule_affected=form.cleaned_data['schedule_affected'],additional_days=form.cleaned_data.get('additional_days'),
        proposed_completion_date=form.cleaned_data['proposed_completion_date'].isoformat() if form.cleaned_data.get('proposed_completion_date') else '')
    return data


def active_base(revision):
    parent=revision.proposal
    if parent.base_revision_id or revision.state!='accepted' or parent.current_revision_id!=revision.pk or parent.has_draft:
        raise ValidationError('Start a change order from the current accepted estimate or proposal.')
    return revision


def approved_orders(base):
    return Proposal.objects.filter(base_revision=base,current_revision__state='accepted').select_related('current_revision').order_by('number')


def amounts(base):
    orders=list(approved_orders(base))
    original=Decimal(base.snapshot['total'])
    added=sum((Decimal(order.current_revision.snapshot['total']) for order in orders),Decimal('0'))
    return {'base_number':base.proposal.number,'base_sequence':base.sequence,'base_title':base.snapshot['title'],
        'original_total':str(original.quantize(Decimal('.01'))),'approved_changes':str(added.quantize(Decimal('.01'))),
        'approved_total':str((original+added).quantize(Decimal('.01'))),'approved_count':len(orders),
        'estimated':base.snapshot.get('pricing')=='hourly' or any(order.current_revision.snapshot.get('pricing')=='hourly' for order in orders)}


def check_send(proposal):
    if not proposal.base_revision_id:
        return
    base=active_base(proposal.base_revision)
    pending=Proposal.objects.filter(base_revision=base,current_revision__state__in=['sent','viewed','changes']).exclude(pk=proposal.pk)
    if pending.exists():
        raise ValidationError('Another change order for this agreement is awaiting a response. Finish or withdraw it before sending another.')


def snapshot_details(proposal,data):
    summary=amounts(proposal.base_revision)
    summary.update(change_total=data['total'],revised_total=str((Decimal(summary['approved_total'])+Decimal(data['total'])).quantize(Decimal('.01'))))
    summary['estimated']=summary['estimated'] or data.get('pricing')=='hourly'
    return {**data,'change_order':summary}


def check_response(proposal,revision):
    check_send(proposal)
    summary=amounts(proposal.base_revision)
    if Decimal(summary['approved_changes'])!=Decimal(revision.snapshot['change_order']['approved_changes']):
        raise ValidationError('Approved pricing changed after this version was sent. Ask us for a revised change order.')


def checked_data(data):
    data=copy.deepcopy(data)
    data['document_kind']='change_order';data['document_style']='phd-studio-v1'
    if data.get('schedule_affected') not in {'yes','no'}:
        raise ValidationError('Choose whether the schedule changes.')
    days=data.get('additional_days')
    if days is not None and (isinstance(days,bool) or not isinstance(days,int) or not 0<=days<=3650):
        raise ValidationError('Review the additional calendar days.')
    try:
        if data.get('proposed_completion_date'):date.fromisoformat(data['proposed_completion_date'])
    except (TypeError,ValueError):
        raise ValidationError('Review the proposed completion date.') from None
    if data['schedule_affected']=='yes' and not data.get('timeline','').strip():
        raise ValidationError('Explain the schedule change for the client.')
    if data['schedule_affected']=='no':
        if days or data.get('proposed_completion_date'):
            raise ValidationError('Choose a schedule change before adding days or a completion date.')
        data['timeline']='No change to the current schedule.'
        data.setdefault('rich_sections',{}).pop('timeline',None)
    return checked_draft(data)


@transaction.atomic
def save_change_order(base_id,data,token,version,actor,proposal_id=None):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    base=ProposalRevision.objects.select_related('proposal__client','proposal__project').get(pk=base_id)
    active_base(base)
    proposal=Proposal.objects.filter(pk=proposal_id or token).first()
    if proposal and not proposal_id:
        if proposal.created_by_id!=actor.pk or proposal.base_revision_id!=base.pk:
            raise ValidationError('This draft belongs to another agreement.')
        return proposal
    if proposal_id and not proposal:
        raise ValidationError('This change order no longer exists.')
    if proposal and (proposal.version!=version or proposal.base_revision_id!=base.pk):
        raise EditConflict('This change order changed. Reload before saving.')
    if proposal and not proposal.has_draft:
        raise ValidationError('Create a draft revision before editing a sent change order.')
    checked=checked_data(data)
    if not proposal:
        counter=Proposal.objects.filter(base_revision__proposal=base.proposal).count()+1
        proposal=Proposal(id=token,base_revision=base,number=f'{base.proposal.number}-CO-{counter:02d}',
            project=base.proposal.project,client=current_client(base.proposal.client),created_by=actor,version=0)
    proposal.title=checked['title'];proposal.draft=checked;proposal.version+=1
    proposal.full_clean();proposal.save()
    event(proposal.project,actor,'Change order draft saved',number=proposal.number,base=base.proposal.number,base_revision=base.sequence)
    return proposal
