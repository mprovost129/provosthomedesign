"""Read-only current proposal versions; no client links or billing side effects."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta
from django.db.models import Case, When, Value, CharField, BooleanField, Q, Exists, OuterRef
from django.utils import timezone
from .models import WorkItem, CLOSED_STATUSES
from .pm_models import Proposal


LABELS = {
    'draft': 'Draft', 'pending': 'Email queued', 'awaiting': 'Awaiting response',
    'changes': 'Changes requested', 'accepted': 'Accepted', 'expired': 'Link expired',
    'withdrawn': 'Withdrawn', 'review': 'Needs review',
}


def overview_queryset(search='', scope='all', now=None):
    now = now or timezone.now()
    jobs = WorkItem.objects.filter(project_id=OuterRef('project_id'))
    unanswered = Q(has_draft=False, current_revision__state__in=['sent', 'viewed'])
    expired = unanswered & Q(current_revision__invitation__expires_at__lte=now)
    email_problem = Q(has_draft=False, current_revision__state__in=['sent', 'viewed', 'changes']) & (
        Q(current_revision__invitation__isnull=True) |
        Q(current_revision__invitation__notification__state__in=['failed', 'unknown', 'canceled']))
    pending = unanswered & Q(current_revision__invitation__notification__state__in=['pending', 'sending'])
    proposals = Proposal.objects.select_related('project', 'client', 'current_revision__invitation__notification').annotate(
        active_project=Exists(jobs.exclude(status__in=CLOSED_STATUSES)),
        email_problem=Case(When(email_problem, then=Value(True)), default=Value(False), output_field=BooleanField()),
        overview_state=Case(
            When(has_draft=True, then=Value('draft')),
            When(current_revision__state='accepted', then=Value('accepted')),
            When(current_revision__state='changes', then=Value('changes')),
            When(current_revision__state='withdrawn', then=Value('withdrawn')),
            When(expired, then=Value('expired')),
            When(email_problem, then=Value('review')),
            When(pending, then=Value('pending')),
            When(unanswered, then=Value('awaiting')),
            default=Value('review'), output_field=CharField(),
        ),
    ).annotate(
        expiring_soon=Case(When(overview_state='awaiting', current_revision__invitation__expires_at__lte=now+timedelta(days=7),
            then=Value(True)), default=Value(False), output_field=BooleanField()),
    )
    if scope == 'active':
        proposals = proposals.filter(active_project=True)
    if search:
        matching_jobs = jobs.filter(Q(reference__icontains=search) | Q(contact_email__icontains=search))
        proposals = proposals.alias(matching_job=Exists(matching_jobs)).filter(
            Q(number__icontains=search) | Q(title__icontains=search) | Q(project__name__icontains=search)
            | Q(project__street__icontains=search) | Q(client__name__icontains=search)
            | Q(current_revision__recipient__icontains=search) | Q(matching_job=True))
    return proposals


def overview_row(proposal):
    revision = proposal.current_revision
    snapshot = proposal.draft if proposal.has_draft else revision.snapshot if revision else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    amount = None
    try:
        candidate = Decimal(str(snapshot.get('total', '')))
        if candidate.is_finite() and 0 <= candidate < Decimal('1000000000'):
            amount = candidate.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        pass
    invitation = getattr(revision, 'invitation', None) if revision else None
    delivery = invitation.notification if invitation else None
    return {
        'proposal': proposal, 'revision': revision, 'invitation': invitation, 'delivery': delivery,
        'state': proposal.overview_state,
        'label': 'Draft revision' if proposal.has_draft and revision else LABELS[proposal.overview_state],
        'amount': amount, 'hourly': snapshot.get('pricing') == 'hourly',
        'valid_through': snapshot.get('valid_until', ''),
    }


ATTENTION = Q(overview_state__in=['changes', 'expired', 'review']) | Q(email_problem=True)
