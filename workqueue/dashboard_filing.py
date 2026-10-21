"""Read-only work-level filing status, shared by dashboard and filing inbox."""
from django.db.models import Count, Exists, OuterRef, Q
from .models import Attachment, Submission, WorkItem, CLOSED_STATUSES
from .dropbox_models import DropboxFiling
from .freshbooks_project_models import FreshBooksProject

FILING_LABELS = {
    'approval': 'Awaiting your approval',
    'dropbox_pending': 'Dropbox transfers in progress',
    'dropbox_attention': 'Dropbox needs attention',
    'freshbooks_pending': 'FreshBooks projects in progress',
    'freshbooks_attention': 'FreshBooks needs attention',
}


def filing_queryset(search=''):
    files = Attachment.objects.filter(submission_id=OuterRef('pk')).exclude(storage_key='')
    pending = Submission.objects.filter(work_item_id=OuterRef('pk'), dropbox_filing__isnull=True).alias(
        stored_files=Exists(files)).filter(Q(stored_files=True) | ~Q(work_item__status__in=CLOSED_STATUSES))
    copies = DropboxFiling.objects.filter(submission__work_item_id=OuterRef('pk'))
    # A connected WorkProject can have multiple queue items; show the approved
    # FreshBooks job on its own source request, not repeatedly on every update.
    projects = FreshBooksProject.objects.filter(work_item_id=OuterRef('pk'))
    items = WorkItem.objects.select_related('project__filing_folder', 'client_contact__client').annotate(
        approval=Exists(pending),
        dropbox_pending=Exists(copies.filter(state__in=['queued','copying'])),
        dropbox_attention=Exists(copies.filter(state='attention')),
        freshbooks_pending=Exists(projects.filter(state__in=['queued','working'])),
        freshbooks_attention=Exists(projects.filter(state__in=['attention','unknown'])),
    )
    if search:
        items = items.filter(Q(reference__icontains=search) | Q(project_name__icontains=search)
            | Q(project__name__icontains=search) | Q(project__street__icontains=search)
            | Q(contact_full_name__icontains=search) | Q(company__icontains=search)
            | Q(project__filing_folder__number__icontains=search))
    return items


def filing_summary():
    totals = filing_queryset().aggregate(**{'count_'+key: Count('pk', filter=Q(**{key:True})) for key in FILING_LABELS})
    return {key:totals['count_'+key] for key in FILING_LABELS}


def row(item):
    return {'item':item, 'flags':[label for key,label in FILING_LABELS.items() if getattr(item,key)],
            'needs_attention':item.dropbox_attention or item.freshbooks_attention}


def unfinished(items):
    condition = Q(pk__isnull=True)
    for key in FILING_LABELS:
        condition |= Q(**{key:True})
    return items.filter(condition)
