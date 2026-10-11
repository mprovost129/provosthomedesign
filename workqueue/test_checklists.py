import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from .models import WorkItem, NotificationDelivery, QueueState
from .pm_models import ProjectTask, ProjectEvent
from .checklist_models import ProjectChecklist, ChecklistApplication
from .checklist_services import save_checklist, apply_checklist, edit_task, checked_titles
from .checklist_forms import ChecklistForm, StepFormSet
from .services import create_work, EditConflict
from .pm_services import change_task
from .tests import work_data


def template_data(**changes):
    return {'name':'Addition workflow','titles':['Review survey','Prepare concept','Review with client'],'archived':False,**changes}


def steps(**changes):
    return [dict(title=title,due_date=None,notes='Private notes',**changes) for title in ['Review survey','Prepare concept']]


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class ChecklistTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('checklist-owner','owner@example.invalid','test-only')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.project=self.item.project
        self.template=save_checklist(uuid.uuid4(),0,template_data(),self.owner)
        self.client.force_login(self.owner)
        self.apply_url=reverse('workqueue:project_checklist',args=[self.project.pk])
        self.task_url=reverse('workqueue:task_list')

    def apply(self, rows=None, token=None, **changes):
        data=dict(project_id=self.project.pk,checklist_id=self.template.pk,version=self.template.version,
            token=token or uuid.uuid4(),rows=rows or steps(),actor=self.owner)
        data.update(changes)
        return apply_checklist(**data)

    def post_rows(self, rows=None, **changes):
        rows=rows or steps()
        data={'template_id':str(self.template.pk),'version':self.template.version,'token':str(uuid.uuid4()),
            'steps-TOTAL_FORMS':len(rows),'steps-INITIAL_FORMS':len(rows),'steps-MIN_NUM_FORMS':0,'steps-MAX_NUM_FORMS':30}
        for index,row in enumerate(rows):
            for key,value in row.items():
                data[f'steps-{index}-{key}']=value or ''
            data[f'steps-{index}-include']='on'
        return {**data,**changes}

    def test_template_creation_edit_archive_and_duplicate_submit(self):
        token=uuid.uuid4()
        created=save_checklist(token,0,template_data(name='Revision'),self.owner)
        replay=save_checklist(token,0,template_data(name='Repeated post'),self.owner)
        self.assertEqual(created.pk,replay.pk)
        self.assertEqual(replay.name,'Revision')
        edited=save_checklist(created.pk,1,template_data(archived=True),self.owner)
        self.assertEqual(edited.version,2)
        with self.assertRaises(EditConflict):
            save_checklist(created.pk,1,template_data(),self.owner)
        response=self.client.get(self.apply_url)
        self.assertNotContains(response, str(edited.pk))
        self.assertContains(self.client.get(reverse('workqueue:checklist_edit',args=[edited.pk])),'Archived')
        save_checklist(edited.pk,2,template_data(),self.owner)
        self.assertContains(self.client.get(self.apply_url),str(edited.pk))

    def test_titles_validate_unicode_whitespace_case_and_limits(self):
        for titles in [[], ['Same step',' same   STEP '], ['ＡＢＣ','abc'], ['x'*201], ['x']*31]:
            with self.assertRaises(ValidationError):
                checked_titles(titles)
        form=ChecklistForm(data={'token':uuid.uuid4(),'version':0,'name':'Example','steps':'A\n\n B\n'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['steps'],['A','B'])
        oversized=StepFormSet({'steps-TOTAL_FORMS':500,'steps-INITIAL_FORMS':0},prefix='steps')
        self.assertFalse(oversized.is_valid())
        self.assertEqual(len(oversized.forms),30)

    def test_preview_has_no_side_effects_and_selected_steps_are_editable(self):
        before=ProjectTask.objects.count()
        response=self.client.get(self.apply_url,{'template':str(self.template.pk)})
        self.assertContains(response,'Review survey')
        self.assertEqual(ProjectTask.objects.count(),before)
        post=self.post_rows()
        post['steps-0-title']='Review existing drawings'
        post['steps-0-due_date']='2026-11-01'
        post.pop('steps-1-include')
        response=self.client.post(self.apply_url,post)
        self.assertEqual(response.status_code,302)
        task=ProjectTask.objects.get()
        self.assertEqual(task.title,'Review existing drawings')
        self.assertEqual(task.due_date.isoformat(),'2026-11-01')

    def test_no_selection_invalid_date_and_incomplete_forms_do_not_write(self):
        for changes in [{'steps-0-due_date':'not-a-date'}, {'steps-TOTAL_FORMS':31}]:
            self.assertEqual(self.client.post(self.apply_url,self.post_rows(**changes)).status_code,400)
        post=self.post_rows()
        post.pop('steps-0-include');post.pop('steps-1-include')
        self.assertEqual(self.client.post(self.apply_url,post).status_code,400)
        self.assertFalse(ProjectTask.objects.exists())
        self.assertFalse(ChecklistApplication.objects.exists())

    def test_apply_is_idempotent_and_independent_of_changed_template_on_retry(self):
        token=uuid.uuid4()
        first=self.apply(token=token)
        save_checklist(self.template.pk,1,template_data(titles=['Different'],archived=True),self.owner)
        replay=self.apply(token=token)
        self.assertEqual(first.pk,replay.pk)
        self.assertEqual(ProjectTask.objects.count(),2)
        self.assertEqual(ChecklistApplication.objects.count(),1)
        self.assertEqual(ProjectEvent.objects.filter(action='Checklist applied').count(),1)

    def test_completed_existing_steps_skip_and_removed_steps_can_be_added(self):
        first=self.apply()
        task=ProjectTask.objects.get(title='Review survey')
        change_task(self.project.pk,task.pk,task.version,'complete',self.owner)
        second=self.apply(rows=[{'title':' review   SURVEY ','notes':'Other','due_date':None}])
        self.assertEqual(second.snapshot['added'],[])
        self.assertEqual(len(second.snapshot['skipped']),1)
        change_task(self.project.pk,task.pk,2,'cancel',self.owner)
        third=self.apply(rows=[{'title':'Review survey','notes':'New round','due_date':None}])
        self.assertEqual(len(third.snapshot['added']),1)
        self.assertEqual(ProjectTask.objects.count(),3)

    def test_changed_and_archived_checklists_require_new_preview(self):
        save_checklist(self.template.pk,1,template_data(archived=True),self.owner)
        response=self.client.post(self.apply_url,self.post_rows())
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'Reload and preview',status_code=409)
        self.assertFalse(ProjectTask.objects.exists())

    def test_tokens_cannot_be_replayed_for_other_project_or_actor(self):
        token=uuid.uuid4();self.apply(token=token)
        other=create_work(data=work_data(project_name='Another'),actor=self.owner)[0]
        another=get_user_model().objects.create_superuser('other-owner','other@example.invalid','test')
        for change in [{'project_id':other.project_id},{'actor':another}]:
            with self.assertRaises(ValidationError):
                self.apply(token=token,**change)

    def test_bad_last_step_rolls_back_entire_application(self):
        rows=steps()
        rows[-1]['notes']='x'*5001
        with self.assertRaises(ValidationError):
            self.apply(rows=rows)
        self.assertFalse(ProjectTask.objects.exists())
        self.assertFalse(ChecklistApplication.objects.exists())

    def test_checklist_changes_do_not_rewrite_project_tasks_or_notify_or_move_jobs(self):
        before=list(WorkItem.objects.values_list('pk','version','queue_order','status'))
        email_count=NotificationDelivery.objects.count()
        self.apply()
        save_checklist(self.template.pk,1,template_data(titles=['Different next step']),self.owner)
        self.assertEqual(set(ProjectTask.objects.values_list('title',flat=True)),{'Review survey','Prepare concept'})
        self.assertEqual(before,list(WorkItem.objects.values_list('pk','version','queue_order','status')))
        self.assertEqual(email_count,NotificationDelivery.objects.count())

    def test_task_edit_preserves_state_and_checks_version(self):
        self.apply();task=ProjectTask.objects.first()
        change_task(self.project.pk,task.pk,1,'complete',self.owner)
        task.refresh_from_db()
        done=task.completed_at
        data={'title':'Updated title','notes':'Private','due_date':timezone.localdate()}
        saved=edit_task(self.project.pk,task.pk,2,data,self.owner)
        self.assertEqual(saved.completed_at,done)
        self.assertEqual(saved.version,3)
        with self.assertRaises(EditConflict):
            edit_task(self.project.pk,task.pk,2,data,self.owner)
        url=reverse('workqueue:task_edit',args=[task.pk])
        self.assertEqual(self.client.post(url,{'action':'cancel','version':3}).status_code,400)
        self.assertEqual(self.client.post(url,{'action':'cancel','version':3,'confirm_remove':'yes'}).status_code,302)
        task.refresh_from_db();self.assertIsNotNone(task.canceled_at)
        self.assertEqual(self.client.post(url,{'action':'reopen','version':4}).status_code,302)
        task.refresh_from_db();self.assertIsNone(task.canceled_at)
        self.assertContains(self.client.get(url),'Updated title')

    def test_http_task_save_invalid_and_stale_safe(self):
        self.apply();task=ProjectTask.objects.first()
        url=reverse('workqueue:task_edit',args=[task.pk])
        data={'action':'save','version':1,'title':'Adjusted','notes':'','due_date':'2026-12-05'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,409)
        self.assertEqual(self.client.post(url,{**data,'version':2,'due_date':'bad'}).status_code,400)
        task.refresh_from_db();self.assertEqual(task.title,'Adjusted')

    def test_due_filters_counts_boundaries_and_multijob_projects(self):
        today=timezone.localdate()
        for index, offset in enumerate([-1,0,6,7,None]):
            ProjectTask.objects.create(project=self.project,title=f'Step {index}',due_date=today+timedelta(days=offset) if offset is not None else None)
        ProjectTask.objects.create(project=self.project,title='Already done',due_date=today-timedelta(days=2),completed_at=timezone.now())
        ProjectTask.objects.create(project=self.project,title='Removed',due_date=today,canceled_at=timezone.now())
        create_work(data=work_data(kind='update',project=self.project),actor=self.owner)
        response=self.client.get(self.task_url)
        self.assertEqual(response.context['totals'],{'open':5,'overdue':1,'today':1,'week':2,'undated':1})
        for view,count in [('overdue',1),('today',1),('week',2),('completed',1),('removed',1),('undated',1)]:
            response=self.client.get(self.task_url,{'view':view})
            self.assertEqual(len(response.context['page']),count)
        WorkItem.objects.filter(project=self.project).update(status='completed')
        self.assertEqual(self.client.get(self.task_url).context['totals']['open'],0)
        self.assertEqual(self.client.get(self.task_url,{'scope':'all'}).context['totals']['open'],5)
        WorkItem.all_objects.filter(project=self.project).update(deleted_at=timezone.now())
        self.assertEqual(self.client.get(self.task_url,{'scope':'all'}).context['totals']['open'],0)

    def test_dashboard_changes_validate_versions_and_preserve_filters(self):
        self.apply();task=ProjectTask.objects.first()
        data={'task_id':task.pk,'version':1,'action':'complete','scope':'all','view':'open','q':'survey'}
        response=self.client.post(self.task_url,data)
        self.assertEqual(response.status_code,302)
        self.assertIn('q=survey',response['Location'])
        self.assertEqual(self.client.post(self.task_url,data).status_code,409)
        self.assertEqual(self.client.post(self.task_url,{**data,'task_id':'bad'}).status_code,400)
        self.assertEqual(self.client.post(self.task_url,{**data,'action':'cancel'}).status_code,400)

    def test_private_permissions_csrf_and_preview(self):
        self.apply();task=ProjectTask.objects.first()
        edit_url=reverse('workqueue:task_edit',args=[task.pk])
        self.client.logout()
        for url in [self.task_url,self.apply_url,edit_url,reverse('workqueue:checklists')]:
            self.assertEqual(self.client.get(url).status_code,302)
        staff=get_user_model().objects.create_user('view-only-checklists',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.task_url).status_code,200)
        self.assertEqual(self.client.get(self.apply_url).status_code,403)
        self.assertEqual(self.client.get(edit_url).status_code,403)
        self.assertEqual(self.client.post(reverse('workqueue:checklists'),{}).status_code,403)
        with self.assertRaises(PermissionDenied):
            self.apply(actor=staff)
        staff.user_permissions.add(Permission.objects.get(codename='change_workitem'))
        with override_settings(WORK_QUEUE_ENABLED=False,WORK_QUEUE_STAFF_PREVIEW=True):
            self.assertEqual(self.client.get(self.task_url).status_code,200)
            self.assertEqual(self.client.post(self.task_url,{}).status_code,403)
        from django.test import Client
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.apply_url,self.post_rows()).status_code,403)

    def test_escaping_and_feature_switch(self):
        self.apply(rows=[{'title':'<script>alert(1)</script>','notes':'<b>Private</b>','due_date':None}])
        response=self.client.get(self.task_url)
        self.assertContains(response,'&lt;script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertIn('no-store',response['Cache-Control'])
        with override_settings(WORK_QUEUE_ENABLED=False):
            self.assertEqual(self.client.get(self.task_url).status_code,404)


@override_settings(WORK_QUEUE_ENABLED=True,WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=False)
class ConcurrentChecklistTests(TransactionTestCase):
    def setUp(self):
        QueueState.objects.get_or_create(pk=1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_applications_create_only_one_copy_of_each_step(self):
        owner=get_user_model().objects.create_superuser('concurrent-list','owner@example.invalid','test')
        project=create_work(data=work_data(),actor=owner)[0].project
        checklist=save_checklist(uuid.uuid4(),0,template_data(),owner)
        def apply(token):
            close_old_connections()
            try:
                return apply_checklist(project.pk,checklist.pk,1,token,steps(),get_user_model().objects.get(pk=owner.pk)).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(apply,[uuid.uuid4(),uuid.uuid4()]))
        self.assertEqual(ProjectTask.objects.count(),2)
        self.assertEqual(ChecklistApplication.objects.count(),2)
        self.assertEqual(sum(len(a.snapshot['added']) for a in ChecklistApplication.objects.all()),2)
