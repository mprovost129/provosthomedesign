import copy
import hashlib
import secrets
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .crm import require_staff
from .models import Client, ClientContact, WorkItem, WorkProject, NotificationDelivery
from .pm_models import ProjectPlan, ProjectTask, Proposal, ProposalRevision, ProposalInvitation, ProposalSequence, ProjectEvent
from .services import EditConflict, lock_queue
from .notifications import site_url


def event(project, actor, action, **details):
    ProjectEvent.objects.create(project=project, actor=actor, action=action, details=details)


def current_client(client):
    seen = set()
    while client.merged_into_id:
        if client.pk in seen:
            raise ValidationError('This client connection needs review.')
        seen.add(client.pk)
        client = client.merged_into
    return client


def client_for_project(project, client_id, proposal=None):
    client = Client.objects.get(pk=client_id, merged_into__isnull=True)
    from .portal_models import ClientProject
    if not WorkItem.objects.filter(project=project, client_contact__client=client).exists() and not ClientProject.objects.filter(project=project, client=client).exists() and not (
            proposal and current_client(proposal.client).pk == client.pk):
        raise ValidationError('Choose a client connected to this project.')
    return client


def checked_proposal(proposal_id, version):
    proposal = Proposal.objects.select_related('project', 'client', 'current_revision').get(pk=proposal_id)
    if proposal.version != version:
        raise EditConflict('This proposal changed. Reload and review the latest version before saving.')
    return proposal


def checked_draft(data):
    result = copy.deepcopy(data)
    kind = result.get('document_kind', 'proposal')
    if kind not in {'estimate', 'proposal', 'change_order'}:
        raise ValidationError('Choose an estimate or proposal.')
    if not result.get('title', '').strip() or (kind in {'proposal','change_order'} and (not result.get('scope', '').strip() or not result.get('deliverables', '').strip())):
        raise ValidationError('Provide a title, scope, and deliverables.')
    if result.get('pricing') not in {'fixed', 'hourly'} or not 1 <= len(result.get('lines', [])) <= 20:
        raise ValidationError('Review the pricing and service lines.')
    date.fromisoformat(result['valid_until'])
    total = Decimal('0')
    for line in result['lines']:
        quantity, rate = Decimal(line['quantity']), Decimal(line['rate'])
        if not quantity.is_finite() or not rate.is_finite() or quantity <= 0 or rate < 0 or not line['description'].strip():
            raise ValidationError('Review quantities, rates, and service descriptions.')
        amount = (quantity * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        line.update(quantity=str(quantity), rate=str(rate), amount=str(amount))
        total += amount
    if total > Decimal('999999999.99'):
        raise ValidationError('The proposal total is too large.')
    result['total'] = str(total.quantize(Decimal('.01')))
    from .document_content import checked_rich
    result['rich_sections'] = checked_rich(result)
    return result


@transaction.atomic
def save_plan(project_id, version, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    project = WorkProject.objects.get(pk=project_id)
    plan = ProjectPlan.objects.filter(project=project).first()
    if (plan.version if plan else 0) != version:
        raise EditConflict('Project details changed. Reload before saving.')
    plan = plan or ProjectPlan(project=project, version=0)
    for name in ['scope', 'internal_notes', 'target_date', 'estimated_hours']:
        setattr(plan, name, data[name])
    plan.version += 1
    plan.full_clean()
    plan.save()
    event(project, actor, 'Project details saved')
    return plan


@transaction.atomic
def save_task(project_id, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    existing = ProjectTask.objects.filter(pk=data['token']).first()
    if existing:
        if existing.project_id != project_id or existing.actor_id != actor.pk:
            raise ValidationError('This task belongs to another project.')
        return existing
    project = WorkProject.objects.get(pk=project_id)
    task = ProjectTask(id=data['token'], project=project, actor=actor,
                       **{name: data[name] for name in ['title', 'due_date', 'notes']})
    task.full_clean()
    task.save()
    event(project, actor, 'Task added', title=task.title)
    return task


@transaction.atomic
def change_task(project_id, task_id, version, action, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    task = ProjectTask.objects.get(pk=task_id, project_id=project_id)
    if task.version != version:
        raise EditConflict('This task changed. Reload before updating it.')
    if action not in {'complete', 'reopen', 'cancel'}:
        raise ValidationError('Choose a task action.')
    task.completed_at = timezone.now() if action == 'complete' else None
    task.canceled_at = timezone.now() if action == 'cancel' else None
    task.version += 1
    task.save()
    event(task.project, actor, 'Task '+{'complete':'completed', 'reopen':'reopened', 'cancel':'removed'}[action], title=task.title)


@transaction.atomic
def save_proposal(project_id, client_id, data, token, version, actor, proposal_id=None, source_id=None):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    proposal = checked_proposal(proposal_id, version) if proposal_id else Proposal.objects.filter(pk=token).first()
    if data.get('document_kind')=='change_order' or (proposal and proposal.base_revision_id):
        raise ValidationError('Use the change order editor linked to the accepted agreement.')
    if proposal and not proposal_id:
        if proposal.created_by_id != actor.pk or proposal.project_id != project_id:
            raise ValidationError('This draft belongs to another project.')
        return proposal
    project = WorkProject.objects.get(pk=project_id)
    client = client_for_project(project, client_id, proposal)
    if proposal and (proposal.project_id != project_id or not proposal.has_draft):
        raise ValidationError('Create a draft revision before editing a sent proposal.')
    data = checked_draft(data)
    source = WorkItem.objects.filter(pk=source_id, project=project).first() if source_id else None
    if source_id and not source:
        raise ValidationError('The source request is no longer connected to this project.')
    if not proposal:
        sequence, _ = ProposalSequence.objects.get_or_create(pk=1)
        sequence = ProposalSequence.objects.select_for_update().get(pk=1)
        sequence.last_number += 1
        sequence.save()
        proposal = Proposal(id=token, number=f'EST-{sequence.last_number:05d}', project=project,
                            client=client, source_item=source, created_by=actor, version=0)
    proposal.client, proposal.draft, proposal.title = client, data, data['title']
    proposal.version += 1
    proposal.save()
    event(project, actor, 'Proposal draft saved', number=proposal.number)
    return proposal


def snapshot_for(proposal):
    data = checked_draft(proposal.draft)
    client, project = current_client(proposal.client), proposal.project
    data.update(number=proposal.number, currency='USD', client_id=str(client.pk), client_name=client.name,
        billing_address='\n'.join(filter(None, [client.billing_street, ' '.join(filter(None, [client.billing_city, client.billing_state, client.billing_zip]))])),
        project_name=project.name, project_reference=project.canonical_reference or '',
        project_address='\n'.join(filter(None, [project.street, ' '.join(filter(None, [project.city, project.state, project.zip_code]))])))
    if data.get('document_style') == 'phd-studio-v1':
        from .document_content import issuer
        data['issuer'] = issuer()
    if proposal.base_revision_id:
        from .change_orders import snapshot_details
        data=snapshot_details(proposal,data)
    return data


def proposal_text(snapshot):
    lines = [f"{snapshot['number']} · {snapshot['title']}", f"Project: {snapshot['project_name']}",
             f"Client: {snapshot['client_name']}", f"Pricing: {'Hourly estimate' if snapshot['pricing']=='hourly' else 'Fixed price'}",
             f"Total: ${snapshot['total']} USD", f"Valid through: {snapshot['valid_until']}"]
    for key, label in [('scope', 'Scope'), ('deliverables', 'Deliverables'), ('revisions', 'Included revisions'), ('notes', 'Notes'),
                       ('exclusions', 'Exclusions'), ('timeline', 'Expected timing'), ('terms', 'Payment and other terms')]:
        if snapshot.get(key):
            lines.append(f"\n{label}\n{snapshot[key]}")
    lines.append('\nServices')
    lines += [f"{line['description']} — {line['quantity']} × ${line['rate']} = ${line['amount']}" for line in snapshot['lines']]
    if snapshot.get('change_order'):
        change=snapshot['change_order']
        lines += ['\nAgreement pricing',f"Original: {change['base_number']} version {change['base_sequence']} — ${change['original_total']}",
            f"Previously approved changes: ${change['approved_changes']}",f"This additional price: ${change['change_total']}",
            f"Revised agreement total{' (estimated)' if change['estimated'] else ''}: ${change['revised_total']} USD",
            f"Schedule change: {'Yes' if snapshot.get('schedule_affected')=='yes' else 'No'}"]
        if snapshot.get('additional_days') is not None:lines.append(f"Additional calendar days (estimate): {snapshot['additional_days']}")
        if snapshot.get('proposed_completion_date'):lines.append('Proposed completion date: '+snapshot['proposed_completion_date'])
    if snapshot['pricing'] == 'hourly':
        lines.append('\nThe total is an estimate based on the listed hours; review the scope and terms for how additional time is handled.')
    return '\n'.join(lines)


@transaction.atomic
def send_proposal(proposal_id, version, contact_id, token, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    existing = ProposalRevision.objects.filter(pk=token).first()
    if existing:
        if existing.proposal_id != proposal_id or existing.actor_id != actor.pk:
            raise ValidationError('This send belongs to another draft.')
        return existing
    proposal = checked_proposal(proposal_id, version)
    if not proposal.has_draft:
        raise ValidationError('This version has already been sent.')
    from .change_orders import check_send
    check_send(proposal)
    client = current_client(proposal.client)
    contact = ClientContact.objects.filter(pk=contact_id, client=client, archived_at__isnull=True).exclude(email__isnull=True).exclude(email='').first()
    if not contact:
        raise ValidationError('Select an active email contact for this client.')
    data = snapshot_for(proposal)
    now = timezone.now()
    valid_until = date.fromisoformat(data['valid_until'])
    if valid_until < now.astimezone(ZoneInfo('America/New_York')).date():
        raise ValidationError('The proposal has expired. Update its valid-through date before sending.')
    expiry = min(now + timedelta(days=30), datetime.combine(valid_until + timedelta(days=1), time.min, ZoneInfo('America/New_York')))
    revision = ProposalRevision.objects.create(id=token, proposal=proposal,
        sequence=(proposal.current_revision.sequence + 1 if proposal.current_revision_id else 1), snapshot=data, recipient=contact.email, actor=actor)
    raw = secrets.token_urlsafe(32)
    link = site_url(reverse('workqueue:proposal_access')) + '#' + urlencode({'token':raw})
    notice = NotificationDelivery.objects.create(recipient_kind='client', recipient=contact.email,
        subject=f"{proposal.number}: {data['title']}",
        body=f"Hello {contact.full_name or client.name},\n\nPlease review your project {data.get('document_kind', 'proposal').replace('_',' ')}:\n{link}\n\n"
             f"This private link expires {expiry.astimezone(ZoneInfo('America/New_York')):%B %d, %Y}. Do not share it.\n\n"
             + proposal_text(data) + '\n\nProvost Home Design\n', provider_reference='queue-'+uuid.uuid4().hex)
    ProposalInvitation.objects.create(revision=revision, digest=hashlib.sha256(raw.encode()).hexdigest(), expires_at=expiry, notification=notice)
    proposal.current_revision, proposal.has_draft = revision, False
    proposal.version += 1
    proposal.save()
    event(proposal.project, actor, 'Proposal emailed', number=proposal.number, revision=revision.sequence, email=contact.email)
    return revision


@transaction.atomic
def revise_proposal(proposal_id, version, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    proposal = checked_proposal(proposal_id, version)
    if proposal.base_revision_id and proposal.current_revision and proposal.current_revision.state=='accepted':
        raise ValidationError('An accepted change order stays fixed. Create another change order for further work.')
    if Proposal.objects.filter(base_revision__proposal=proposal,current_revision__state__in=['sent','viewed','changes','accepted']).exists():
        raise ValidationError('This agreement has pending or accepted change orders. Use a change order for additional work.')
    if proposal.has_draft:
        raise ValidationError('There is already a draft to edit.')
    proposal.draft = {key:copy.deepcopy(proposal.current_revision.snapshot[key]) for key in
        ['title', 'pricing', 'scope', 'deliverables', 'revisions', 'exclusions', 'timeline', 'terms', 'valid_until', 'lines', 'total']}
    for key in ('document_kind','document_style','issue_date','document_reference','notes','rich_sections','schedule_affected','additional_days','proposed_completion_date'):
        if key in proposal.current_revision.snapshot:
            proposal.draft[key] = copy.deepcopy(proposal.current_revision.snapshot[key])
    proposal.has_draft = True
    proposal.version += 1
    proposal.save()
    event(proposal.project, actor, 'Proposal revision started', number=proposal.number)
    return proposal


@transaction.atomic
def withdraw_proposal(proposal_id, version, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    proposal = checked_proposal(proposal_id, version)
    revision = proposal.current_revision
    if not revision or revision.state not in {'sent', 'viewed', 'changes'}:
        raise ValidationError('Only an unaccepted sent version can be withdrawn.')
    revision.state = 'withdrawn'
    revision.save(update_fields=['state'])
    NotificationDelivery.objects.filter(pk=revision.invitation.notification_id, state='pending').update(
        state='canceled', body='', html_body='', last_error='Proposal withdrawn before delivery.')
    proposal.version += 1
    proposal.save()
    event(proposal.project, actor, 'Proposal withdrawn', number=proposal.number, revision=revision.sequence)


def valid_invitation(invitation_id):
    invite = ProposalInvitation.objects.select_related('revision__proposal__current_revision').filter(pk=invitation_id, expires_at__gt=timezone.now()).first()
    if not invite:
        raise ValidationError('This private link expired. Ask us for a new proposal link.')
    return invite


@transaction.atomic
def respond(invitation_id, data):
    lock_queue()
    invite = valid_invitation(invitation_id)
    revision, proposal = invite.revision, invite.revision.proposal
    if proposal.current_revision_id != revision.pk or proposal.has_draft or revision.state == 'withdrawn':
        raise ValidationError('This version is no longer available for acceptance. Please contact us about the latest proposal.')
    if revision.state == 'accepted':
        return revision
    if proposal.base_revision_id:
        from .change_orders import check_response
        check_response(proposal,revision)
    if revision.state == 'changes':
        raise ValidationError('Your change request is recorded. Please wait for a revised proposal.')
    action = data['action']
    if action not in {'accept', 'changes'} or not data.get('full_name') or (action == 'accept' and not data.get('agreement')):
        raise ValidationError('Review the proposal and complete the response fields.')
    if action == 'changes' and not data.get('note'):
        raise ValidationError('Describe the requested changes.')
    revision.state = 'accepted' if action == 'accept' else 'changes'
    revision.accepted_at = timezone.now() if action == 'accept' else None
    revision.accepted_name = data['full_name']
    revision.response_note = data.get('note', '')
    revision.save(update_fields=['state', 'accepted_at', 'accepted_name', 'response_note'])
    proposal.version += 1
    proposal.save()
    label = 'accepted' if action == 'accept' else 'changes requested'
    body = (f"{proposal.number} version {revision.sequence}: {label}\n"
            f"By: {revision.accepted_name}\nEmail: {revision.recipient}\n\nMessage:\n{revision.response_note or 'None'}\n\n"
            + proposal_text(revision.snapshot))
    from django.conf import settings
    for kind, email in [('client', revision.recipient), ('owner', settings.INTAKE_OWNER_EMAIL)]:
        if email:
            NotificationDelivery.objects.create(recipient_kind=kind, recipient=email,
                subject=f'{proposal.number}: {label}', body=body, provider_reference='queue-'+uuid.uuid4().hex)
    event(proposal.project, None, ('Change order ' if proposal.base_revision_id else 'Proposal ')+label, number=proposal.number, revision=revision.sequence, email=revision.recipient)
    return revision
