"""Authoritative Decimal calculations and explicit, idempotent proposal creation."""
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from .crm import require_staff
from .models import Client, WorkProject, ClientEvent
from .pm_services import client_for_project, save_proposal, event
from .services import lock_queue, EditConflict
from .pricing_models import PricingRateVersion, PricingSequence, PricingEstimate, PricingEstimateRevision

SERVICES = [
    ('new', 'New construction', 'Living space, including finished basements.'),
    ('attached', 'Attached garage / large unfinished space', 'Exclude unfinished basements. Keep this area out of living-space area.'),
    ('detached', 'Detached garage', 'Garage footprint plus the area of the floor above, if applicable.'),
    ('addition', 'Addition design', 'New living space in the addition.'),
    ('existing', 'Draw existing structure', 'The entire area you need drawn as existing, including the garage.'),
    ('renovation', 'Renovation design', 'Only the area being renovated. This is a separate service from drawing the existing structure.'),
]
RATE_KEYS = [key for key, _, _ in SERVICES] + ['hourly']
CONTRACTOR_RATES = {'new':'1.25', 'attached':'.75', 'detached':'1.25', 'addition':'1.25', 'existing':'.75', 'renovation':'.75', 'hourly':'125.00'}
CATEGORIES = [('homeowner','Homeowner'), ('contractor','Contractor'), ('other','Other')]
MAX_TOTAL = Decimal('999999999.99')


def number(value, *, maximum=Decimal('999999.99')):
    try:
        result = Decimal(str(value or '0'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Enter a valid number.') from None
    if not result.is_finite() or result < 0 or result > maximum or result.as_tuple().exponent < -2:
        raise ValidationError('Use nonnegative numbers with at most two decimal places, within the field limit.')
    return result


def money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def latest_card(category):
    return PricingRateVersion.objects.filter(category=category).order_by('-sequence').first()


def calculate(data, card):
    if data.get('mode') not in {'area','hourly'}:
        raise ValidationError('Choose square-foot or hourly pricing.')
    category = data.get('category')
    if category not in dict(CATEGORIES) or card.category != category:
        raise ValidationError('Choose a configured rate card matching the client pricing category.')
    lines, custom_rates = [], False
    def line(key, description, quantity):
        nonlocal custom_rates
        rate = number(data.get(key+'_rate', card.rates[key]), maximum=Decimal('99999999.99'))
        if quantity and rate != number(card.rates[key], maximum=Decimal('99999999.99')):
            custom_rates = True
        if quantity:
            lines.append({'key':key, 'description':description, 'quantity':str(quantity),
                          'rate':str(rate), 'amount':str(money(quantity*rate))})
    if data['mode'] == 'hourly':
        line('hourly', 'Estimated design time (hours)', number(data.get('hours')))
    else:
        for key, label, _ in SERVICES:
            quantity = number(data.get(key+'_area'))
            if key == 'detached':
                quantity += number(data.get('detached_above_area'))
                if quantity > Decimal('999999.99'):
                    raise ValidationError('Review the combined detached garage and upper-floor area.')
            line(key, label+' (sq ft)', quantity)
    if not lines:
        raise ValidationError('Enter at least one service area or a positive number of estimated hours.')
    if custom_rates and not data.get('rate_reason','').strip():
        raise ValidationError('Explain the one-time rate adjustment in the private rate notes.')
    if data['mode'] == 'area':
        custom = number(data.get('custom_fee'), maximum=Decimal('99999999.99'))
        if custom:
            description = data.get('custom_description','').strip()
            if not description:
                raise ValidationError('Describe the additional fixed-price service.')
            lines.append({'key':'custom', 'description':description, 'quantity':'1', 'rate':str(custom), 'amount':str(money(custom))})
    subtotal = sum((Decimal(line['amount']) for line in lines), Decimal(0))
    override = data.get('override_total')
    overridden = data['mode'] == 'area' and override is not None and override != ''
    total = number(override, maximum=MAX_TOTAL) if overridden else subtotal
    if overridden and not data.get('override_reason','').strip():
        raise ValidationError('Explain the quoted fixed-fee override in the private adjustment notes.')
    if subtotal > MAX_TOTAL or total > MAX_TOTAL:
        raise ValidationError('The total is too large. Review the quantities and rates.')
    return {'lines':lines, 'subtotal':str(money(subtotal)), 'total':str(money(total)),
            'overridden':overridden, 'custom_rates':custom_rates,
            'pricing':'hourly' if data['mode']=='hourly' else 'fixed'}


def check_assignment(client, project, category):
    if client:
        if client.merged_into_id:
            raise ValidationError('Choose the current, unmerged client record.')
        if not client.pricing_category:
            raise ValidationError('Set Homeowner, Contractor or Other on this client record before saving a calculation for them.')
        if client.pricing_category != category:
            raise ValidationError('The client pricing category changed. Reload and review the matching rates.')
    if client and project:
        client_for_project(project, client.pk)


@transaction.atomic
def save_estimate(*, data, token, version, actor, estimate_id=None):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    estimate = PricingEstimate.objects.select_for_update().filter(pk=estimate_id or token).first()
    if estimate and not estimate_id:
        if estimate.created_by_id != actor.pk:
            raise ValidationError('This calculation belongs to another staff member.')
        return estimate
    if estimate_id and (not estimate or estimate.version != version):
        raise EditConflict('This calculation changed. Reload before saving another revision.')
    client = Client.objects.get(pk=data['client'].pk) if data.get('client') else None
    project = WorkProject.objects.get(pk=data['project'].pk) if data.get('project') else None
    check_assignment(client, project, data['category'])
    card = PricingRateVersion.objects.get(pk=data['rate_version'].pk)
    result = calculate(data, card)
    inputs = {key:(str(value) if isinstance(value, Decimal) else value) for key,value in data.items()
              if key not in {'client','project','rate_version','token','version'}}
    if not estimate:
        sequence,_ = PricingSequence.objects.get_or_create(pk=1)
        sequence = PricingSequence.objects.select_for_update().get(pk=1)
        sequence.last_number += 1
        sequence.save(update_fields=['last_number'])
        estimate = PricingEstimate(id=token, number=f'CALC-{sequence.last_number:05d}', version=0, created_by=actor)
    estimate.title,estimate.client,estimate.project = data['title'],client,project
    estimate.version += 1
    estimate.save()
    snapshot = {'inputs':inputs, 'result':result, 'category':data['category'],
        'client_id':str(client.pk) if client else '', 'client_name':client.name if client else '',
        'project_id':str(project.pk) if project else '', 'project_name':project.name if project else '',
        'rate_label':str(card), 'rates':dict(card.rates)}
    revision = PricingEstimateRevision.objects.create(estimate=estimate, sequence=estimate.version,
        rate_version=card, snapshot=snapshot, created_by=actor)
    estimate.current_revision = revision
    estimate.save(update_fields=['current_revision'])
    if project:
        event(project, actor, 'Pricing calculation saved', number=estimate.number, revision=revision.sequence)
    elif client:
        ClientEvent.objects.create(client=client, actor=actor, action='Pricing calculation saved', changes={'number':estimate.number})
    return estimate


@transaction.atomic
def save_rate_card(*, category, label, rates, source_note, expected, actor):
    from django.core.exceptions import PermissionDenied
    require_staff(actor,'workqueue.change_workitem')
    if not actor.is_superuser:
        raise PermissionDenied
    lock_queue()
    if category not in dict(CATEGORIES) or set(rates) != set(RATE_KEYS):
        raise ValidationError('Provide a complete rate card for one pricing category.')
    current = latest_card(category)
    if (current.sequence if current else 0) != expected:
        raise EditConflict('The rate card changed. Reload before creating another version.')
    checked = {key:str(money(number(value, maximum=Decimal('99999999.99')))) for key,value in rates.items()}
    return PricingRateVersion.objects.create(category=category, sequence=expected+1,
        label=label, rates=checked, source_note=source_note, created_by=actor)


@transaction.atomic
def create_proposal(*, estimate_id, version, data, actor):
    require_staff(actor,'workqueue.change_workitem')
    lock_queue()
    estimate = PricingEstimate.objects.select_for_update(of=('self',)).select_related('client','project','current_revision__proposal').get(pk=estimate_id)
    if estimate.version != version:
        raise EditConflict('The calculation changed. Reload and review the latest revision.')
    revision = estimate.current_revision
    if revision.proposal_id:
        return revision.proposal
    if not data.get('review_confirmed'):
        raise ValidationError('Review the calculation before creating a proposal draft.')
    if not estimate.client_id or not estimate.project_id:
        raise ValidationError('Assign this calculation to a client and their project before creating a proposal.')
    check_assignment(estimate.client,estimate.project,revision.snapshot['category'])
    if revision.snapshot['inputs'].get('project_review') == 'custom' and (
            not data.get('review_confirmed') or not data.get('review_note','').strip()):
        raise ValidationError('Confirm the individual project review and record why the proposed price is appropriate.')
    result = revision.snapshot['result']
    lines = [{key:line[key] for key in ['description','quantity','rate']} for line in result['lines']]
    if result['overridden']:
        # Do not disclose internal guideline prices or private adjustment reasons.
        lines = [{'description':'Residential design services — agreed fixed fee', 'quantity':'1', 'rate':result['total']}]
    proposal_data = {key:data.get(key,'') for key in ['title','scope','deliverables','revisions','exclusions','timeline','terms']}
    proposal_data.update(pricing=result['pricing'], lines=lines, valid_until=data['valid_until'].isoformat())
    proposal = save_proposal(estimate.project_id, estimate.client_id, proposal_data,
        uuid.uuid4(), 0, actor)
    revision.proposal = proposal
    revision.save(update_fields=['proposal'])
    event(estimate.project, actor, 'Proposal created from pricing calculation', calculation=estimate.number,
          calculation_revision=revision.sequence, proposal=proposal.number,
          individual_review=data.get('review_note','').strip())
    return proposal
