"""Project-level planning; estimates never alter a client's queue position."""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import OuterRef, Subquery, Min
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import WorkItem, CLOSED_STATUSES
from .pm_models import ProjectTask
from .pm_views import staff_view
from .reporting import project_report_queryset, hours


def monday(day):
    return day-timedelta(days=day.weekday())


def workload_board(start, today):
    end=start+timedelta(days=28)
    weeks=[{'start':start+timedelta(days=7*i),'end':start+timedelta(days=7*i+6),
        'rows':[],'hours':Decimal(0),'unknown':0,'count':0,'tasks':0} for i in range(4)]
    earlier={'rows':[],'hours':Decimal(0),'unknown':0,'count':0,'tasks':0}
    unscheduled={'rows':[],'hours':Decimal(0),'unknown':0,'count':0,'tasks':0}
    later={'rows':[],'hours':Decimal(0),'unknown':0,'count':0,'tasks':0}
    jobs=WorkItem.objects.filter(project_id=OuterRef('pk')).exclude(status__in=CLOSED_STATUSES).order_by().values('project_id')
    projects=project_report_queryset().filter(active_count__gt=0).select_related('filing_folder').annotate(
        first_due=Subquery(jobs.annotate(day=Min('committed_due_date')).values('day')[:1]),
        first_start=Subquery(jobs.annotate(day=Min('scheduled_start')).values('day')[:1]),
    ).order_by('name','pk')
    for project in projects:
        plan=getattr(project,'plan',None)
        dates=[day for day in [project.first_due,plan.target_date if plan else None] if day]
        due=min(dates) if dates else None
        target=due or project.first_start
        bucket=(unscheduled if target is None else earlier if target<start else later if target>=end
            else weeks[(target-start).days//7])
        estimate=plan.estimated_hours if plan else None
        remaining=None if estimate is None else max(Decimal(0),estimate-Decimal(project.seconds)/3600)
        bucket['count']+=1
        if remaining is None:bucket['unknown']+=1
        else:bucket['hours']+=remaining
        if len(bucket['rows'])<30:
            bucket['rows'].append({'project':project,'target':target,'date_label':'Due' if due else 'Starts',
                'overdue':bool(due and due<today),'estimate':estimate,'recorded':hours(project.seconds),
                'remaining':None if remaining is None else remaining.quantize(Decimal('.01')),
                'over_budget':estimate is not None and Decimal(project.seconds)>estimate*3600})
    # Tasks add context, not extra estimated project hours.
    for task in ProjectTask.objects.filter(completed_at__isnull=True,canceled_at__isnull=True,due_date__gte=start,due_date__lt=end).values_list('due_date',flat=True):
        weeks[(task-start).days//7]['tasks']+=1
    for bucket in [*weeks,earlier,unscheduled,later]:
        bucket['hours']=bucket['hours'].quantize(Decimal('.01'))
        bucket['hidden_count']=bucket['count']-len(bucket['rows'])
    return {'weeks':weeks,'earlier':earlier,'unscheduled':unscheduled,'later':later,
        'unlinked_count':WorkItem.objects.filter(project__isnull=True).exclude(status__in=CLOSED_STATUSES).count(),
        'known_hours':sum((week['hours'] for week in weeks),Decimal(0)),
        'unknown_count':sum(week['unknown'] for week in weeks)}


@staff_view
@require_GET
def workload(request):
    today=timezone.localdate()
    try:
        selected=date.fromisoformat(request.GET.get('week',''))
        # Bound navigation so previous/next always stay within date arithmetic.
        if not 2001<=selected.year<=2098:raise ValueError
    except (ValueError,TypeError):selected=today
    start=monday(selected)
    context=workload_board(start,today)
    context.update({'week_start':start,'previous':start-timedelta(days=28),'next':start+timedelta(days=28),'today':today})
    return render(request,'workqueue/workload.html',context)
