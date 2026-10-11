from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from .crm import require_staff
from .models import WorkProject, WorkItem
from .pm_services import event
from .services import EditConflict, lock_queue
from .time_models import TimeEntry


def active_timer(actor):
    return TimeEntry.objects.filter(actor=actor, source='timer', ended_at__isnull=True,
        deleted_at__isnull=True).select_related('project', 'work_item').first()


def checked_fields(project_id, data):
    item = data.get('work_item')
    if item and not WorkItem.all_objects.filter(pk=item.pk, project_id=project_id).exists():
        raise ValidationError('Choose a job belonging to this project.')
    return {key:data[key] for key in ['work_item', 'category', 'description', 'billable']}


@transaction.atomic
def start_timer(project_id, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    existing = TimeEntry.objects.filter(pk=data['token']).first()
    if existing:
        if existing.actor_id != actor.pk or existing.project_id != project_id or existing.source != 'timer':
            raise ValidationError('This timer request belongs to another record.')
        return existing
    if active_timer(actor):
        raise EditConflict('You already have a timer running. Stop it before starting another.')
    if not WorkItem.objects.filter(project_id=project_id).exists():
        raise ValidationError('Choose a project with a current job before starting a timer.')
    if data.get('work_item') and data['work_item'].deleted_at:
        raise ValidationError('Choose a current job before starting a timer.')
    now = timezone.now()
    entry = TimeEntry(id=data['token'], project_id=project_id, actor=actor, source='timer',
        started_at=now, work_date=timezone.localdate(now), **checked_fields(project_id, data))
    entry.full_clean(); entry.save()
    event(entry.project, actor, 'Timer started', entry=str(entry.pk))
    return entry


@transaction.atomic
def stop_timer(entry_id, actor, discard=False):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    entry = TimeEntry.objects.get(pk=entry_id, actor=actor, source='timer')
    if not entry.running:
        return entry  # A retry from another tab cannot stop a newer timer.
    now = timezone.now()
    if now < entry.started_at:
        raise ValidationError('The timer start is later than the server clock. Review this record.')
    if discard:
        entry.deleted_at = now
    else:
        entry.ended_at = now
        entry.duration_seconds = max(1, int((now-entry.started_at).total_seconds()))
    entry.version += 1
    entry.full_clean(); entry.save()
    event(entry.project, actor, 'Timer discarded' if discard else 'Time recorded',
        entry=str(entry.pk), seconds=entry.duration_seconds)
    return entry


@transaction.atomic
def save_time(project_id, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    entry = TimeEntry.objects.filter(pk=data['token']).first()
    version = data['version']
    if entry:
        if entry.project_id != project_id:
            raise ValidationError('This time entry belongs to another project.')
        if version == 0 and entry.actor_id == actor.pk and entry.source == 'manual':
            return entry  # Idempotent manual entry, including double-click/retry.
        if entry.version != version:
            raise EditConflict('This time entry changed. Reload before editing it.')
        if entry.running or entry.deleted_at:
            raise ValidationError('Stop the timer or restore this entry before editing it.')
    elif version != 0:
        raise EditConflict('This time entry is no longer available.')
    else:
        entry = TimeEntry(id=data['token'], project=WorkProject.objects.get(pk=project_id), actor=actor, version=0)
    date = data['work_date']
    try:
        minutes = Decimal(data['minutes'])
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('Enter a valid duration in minutes.') from None
    if not minutes.is_finite() or not Decimal('.01') <= minutes <= 1440 or date > timezone.localdate():
        raise ValidationError('Record actual time for today or an earlier date, up to 24 hours per entry.')
    before = dict(seconds=entry.duration_seconds, date=entry.work_date.isoformat(), category=entry.category,
                  billable=entry.billable, description=entry.description, job=str(entry.work_item_id or '')) if version else None
    for key, value in checked_fields(project_id, data).items():
        setattr(entry, key, value)
    entry.duration_seconds = max(1, int((minutes*60).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
    entry.work_date = date
    entry.version += 1
    entry.full_clean(); entry.save()
    event(entry.project, actor, 'Time entry corrected' if version else 'Time added manually',
        entry=str(entry.pk), before=before, seconds=entry.duration_seconds, date=entry.work_date.isoformat(),
        category=entry.category, billable=entry.billable, description=entry.description, job=str(entry.work_item_id or ''))
    return entry


@transaction.atomic
def archive_time(project_id, entry_id, version, actor, restore=False):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    entry = TimeEntry.objects.get(pk=entry_id, project_id=project_id)
    if entry.version != version:
        raise EditConflict('This time entry changed. Reload before updating it.')
    if entry.running or entry.duration_seconds is None:
        raise ValidationError('Stop the timer first. Discarded timers cannot become recorded time.')
    entry.deleted_at = None if restore else timezone.now()
    entry.version += 1
    entry.full_clean(); entry.save()
    event(entry.project, actor, 'Time entry restored' if restore else 'Time entry removed', entry=str(entry.pk))
    return entry


def time_summary(project):
    totals = project.time_entries.filter(deleted_at__isnull=True, duration_seconds__isnull=False).aggregate(
        total=Sum('duration_seconds'), billable=Sum('duration_seconds', filter=Q(billable=True)))
    seconds = totals['total'] or 0
    billable = totals['billable'] or 0
    def hours(value):
        return (Decimal(value)/3600).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    plan = getattr(project, 'plan', None)
    estimated = plan.estimated_hours if plan else None
    result = dict(total=hours(seconds), billable=hours(billable), nonbillable=hours(seconds-billable), estimated=estimated)
    if estimated is not None:
        result.update(over_budget=Decimal(seconds)/3600 > estimated,
            remaining=(estimated-Decimal(seconds)/3600).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    return result
