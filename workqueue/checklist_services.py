import unicodedata
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import WorkProject, WorkItem
from .pm_models import ProjectTask
from .pm_services import event
from .crm import require_staff
from .services import EditConflict, lock_queue
from .checklist_models import ProjectChecklist, ChecklistApplication


def title_key(title):
    return ' '.join(unicodedata.normalize('NFKC', title).split()).casefold()


def checked_notes(notes):
    if not isinstance(notes, str) or len(notes) > 5000:
        raise ValidationError('Task notes must be text of up to 5,000 characters.')
    return notes


def checked_titles(titles):
    if not isinstance(titles, list) or not 1 <= len(titles) <= 30:
        raise ValidationError('Enter between 1 and 30 steps, one per line.')
    result, keys = [], set()
    for title in titles:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValidationError('Each step needs a title of up to 200 characters.')
        title = title.strip()
        key = title_key(title)
        if key in keys:
            raise ValidationError('Remove repeated steps from the checklist.')
        keys.add(key)
        result.append(title)
    return result


@transaction.atomic
def save_checklist(token, version, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    checklist = ProjectChecklist.objects.filter(pk=token).first()
    if checklist and version == 0:
        if checklist.created_by_id != actor.pk:
            raise ValidationError('This checklist was created by another staff member.')
        return checklist
    if (checklist.version if checklist else 0) != version:
        raise EditConflict('This checklist changed. Reload before saving.')
    checklist = checklist or ProjectChecklist(id=token, version=0, created_by=actor)
    checklist.name = data['name'].strip()
    checklist.titles = checked_titles(data['titles'])
    checklist.archived = data['archived']
    checklist.version += 1
    checklist.full_clean()
    checklist.save()
    return checklist


@transaction.atomic
def apply_checklist(project_id, checklist_id, version, token, rows, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    existing = ChecklistApplication.objects.filter(pk=token).first()
    if existing:
        if existing.project_id != project_id or existing.checklist_id != checklist_id or existing.actor_id != actor.pk:
            raise ValidationError('This application belongs to another project or staff member.')
        return existing
    checklist = ProjectChecklist.objects.get(pk=checklist_id)
    if checklist.version != version or checklist.archived:
        raise EditConflict('The checklist changed. Reload and preview it again before applying.')
    if not 1 <= len(rows) <= 30:
        raise ValidationError('Select between 1 and 30 steps.')
    checked_titles([row['title'] for row in rows])
    project = WorkProject.objects.get(pk=project_id)
    if not WorkItem.objects.filter(project=project).exists():
        raise ValidationError('This project has no current jobs. Restore or add a job first.')
    used = {title_key(t) for t in project.tasks.filter(canceled_at__isnull=True).values_list('title', flat=True)}
    snapshot = {'selected':[], 'added':[], 'skipped':[]}
    for row in rows:
        values = {key:row[key] for key in ['title','notes','due_date']}
        values['title'] = values['title'].strip()
        values['notes'] = checked_notes(values['notes'])
        task = ProjectTask(id=uuid.uuid4(), project=project, actor=actor, **values)
        task.full_clean()
        snapshot['selected'].append({'title':task.title, 'notes':task.notes,
            'due_date':task.due_date.isoformat() if task.due_date else None})
        key = title_key(values['title'])
        if key in used:
            snapshot['skipped'].append(values['title'])
            continue
        task.save()
        snapshot['added'].append(str(task.pk))
        used.add(key)
    application = ChecklistApplication.objects.create(id=token, project=project, checklist=checklist,
        checklist_version=version, actor=actor, snapshot=snapshot)
    event(project, actor, 'Checklist applied', title=checklist.name,
        added=len(snapshot['added']), skipped=len(snapshot['skipped']), application=str(application.pk))
    return application


@transaction.atomic
def edit_task(project_id, task_id, version, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    task = ProjectTask.objects.get(pk=task_id, project_id=project_id)
    if task.version != version:
        raise EditConflict('This task changed. Reload before saving.')
    before = {'title':task.title, 'notes':task.notes, 'due_date':str(task.due_date) if task.due_date else None}
    for key in ['title','due_date','notes']:
        setattr(task, key, data[key])
    task.title = task.title.strip()
    task.notes = checked_notes(task.notes)
    if not task.title:
        raise ValidationError('Enter a task title.')
    task.full_clean()
    task.version += 1
    task.save()
    event(task.project, actor, 'Task edited', title=task.title, before=before,
        after={'title':task.title,'notes':task.notes,'due_date':str(task.due_date) if task.due_date else None})
    return task
