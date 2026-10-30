import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .forms import ManualWorkForm
from .models import ClientContact, NotificationDelivery, ProjectAccess, Status, Submission, WorkItem, WorkProject
from .queue_project_updates import create_manual_work, initial_for_update, project_defaults
from .services import create_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=False, BOOKING_ENABLED=False)
class QueueProjectUpdatesTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('owner','owner@example.com','test-password')
        self.old=create_work(data=work_data(project_name='Lot 10',service_needed='Plan changes'),actor=None)[0]
        WorkItem.objects.filter(pk=self.old.pk).update(status=Status.COMPLETED)
        self.old.refresh_from_db()
        self.project=self.old.project
        self.project.name='Lot 12';self.project.street='12 Corrected Street';self.project.save()
        self.url=reverse('workqueue:queue')
        self.defaults_url=reverse('workqueue:queue_project_defaults',args=[self.project.pk])
        self.client.force_login(self.owner)

    def post_data(self,**changes):
        data=initial_for_update(self.project)
        data['submission_token']=uuid.uuid4()
        data.update(changes)
        return {'action':'create',**{'new-'+key:str(value) if value is not None else '' for key,value in data.items()}}

    def test_completed_project_in_selector_and_get_shortcut_does_not_create(self):
        response=self.client.get(self.url,{'scope':'all','update_project':self.project.pk,'source_request':self.old.pk})
        self.assertEqual(response.status_code,200)
        form=response.context['create_form']
        self.assertEqual(form.initial['kind'],'update')
        self.assertEqual(form.initial['project_name'],'Lot 12')
        self.assertEqual(form.initial['description'],self.old.description)
        self.assertTrue(response.context['create_open'])
        self.assertContains(response,'Add update')
        self.assertContains(response,'Lot 12')
        self.assertEqual(WorkItem.objects.count(),1)

    def test_lookup_current_crm_and_canonical_project_and_no_internal_fields(self):
        contact=self.old.client_contact
        contact.full_name='Current contact';contact.email='current@example.com';contact.phone='5550001';contact.save()
        client=contact.client;client.billing_city='Current city';client.save()
        response=self.client.get(self.defaults_url)
        self.assertEqual(response.status_code,200)
        data=response.json();fields=data['fields']
        self.assertEqual(fields['project_name'],'Lot 12')
        self.assertEqual(fields['project_street'],'12 Corrected Street')
        self.assertEqual(fields['contact_full_name'],'Current contact')
        self.assertEqual(fields['contact_email'],'current@example.com')
        self.assertEqual(fields['billing_city'],'Current city')
        self.assertEqual(fields['requested_deadline'],'')
        self.assertTrue(data['contact_known'])
        self.assertEqual(set(fields),set(initial_for_update(self.project))-{'project','kind','update_source'})
        self.assertNotIn('internal_notes',response.content.decode())
        self.assertIn('no-store',response['Cache-Control'])

    def test_new_update_own_position_no_original_mutation_or_notifications(self):
        data=self.post_data(kind='new',description='Only the new changes',service_needed='Renovation')
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302)
        update=WorkItem.objects.exclude(pk=self.old.pk).get()
        self.assertEqual(update.kind,'update')
        self.assertEqual(update.project_id,self.project.pk)
        self.assertEqual(update.previous_request_id,self.old.pk)
        self.assertEqual(update.client_contact_id,self.old.client_contact_id)
        self.assertEqual(update.description,'Only the new changes')
        self.assertEqual(update.service_needed,'Renovation')
        self.assertEqual(update.status,Status.NEW)
        self.assertEqual(update.submission_position,1)
        self.assertNotEqual(update.reference,self.old.reference)
        self.old.refresh_from_db();self.project.refresh_from_db()
        self.assertEqual(self.old.status,Status.COMPLETED)
        self.assertNotEqual(self.old.description,update.description)
        self.assertEqual(self.project.name,'Lot 12')
        self.assertEqual(WorkProject.objects.count(),1)
        self.assertEqual(ClientContact.objects.count(),1)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertFalse(ProjectAccess.objects.exists())
        self.assertEqual(len(mail.outbox),0)
        self.client.post(self.url,data)
        self.assertEqual(WorkItem.objects.count(),2)

    def test_explicit_contact_edits_are_snapshot_only(self):
        response=self.client.post(self.url,self.post_data(contact_full_name='Different request name',contact_email='request@example.com',project_name='Request label'))
        self.assertEqual(response.status_code,302)
        update=WorkItem.objects.exclude(pk=self.old.pk).get()
        self.assertEqual(update.contact_email,'request@example.com')
        update.client_contact.refresh_from_db();self.project.refresh_from_db()
        self.assertEqual(update.client_contact.email,'morgan@example.com')
        self.assertEqual(self.project.name,'Lot 12')

    def test_known_contact_with_missing_email_phone_can_be_reused(self):
        contact=self.old.client_contact;contact.email=None;contact.phone='';contact.save()
        response=self.client.post(self.url,self.post_data())
        self.assertEqual(response.status_code,302)
        update=WorkItem.objects.exclude(pk=self.old.pk).get()
        self.assertEqual(update.contact_email,'')
        self.assertEqual(update.client_contact_id,contact.pk)
        self.assertEqual(ClientContact.objects.count(),1)

    def test_server_load_button_never_submits_or_allocates(self):
        response=self.client.post(self.url,{'action':'create','load_project':'1','new-project':self.project.pk,'new-submission_token':uuid.uuid4()})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['create_form'].initial['project_name'],'Lot 12')
        self.assertEqual(WorkItem.objects.count(),1)
        self.assertEqual(Submission.objects.count(),1)

    def test_invalid_load_preserves_draft(self):
        data=self.post_data(project='invalid',description='Keep my draft');data['load_project']='1'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.context['create_form']['description'].value(),'Keep my draft')
        self.assertEqual(WorkItem.objects.count(),1)

    def test_validation_failure_preserves_explicit_description_and_service(self):
        response=self.client.post(self.url,self.post_data(description='My draft',service_needed='My service',contact_email='not-an-email'))
        self.assertEqual(response.status_code,400)
        form=response.context['create_form']
        self.assertEqual(form['description'].value(),'My draft')
        self.assertEqual(form['service_needed'].value(),'My service')
        self.assertEqual(WorkItem.objects.count(),1)

    def test_source_must_belong_to_project(self):
        other=create_work(data=work_data(project_name='Other'),actor=None)[0]
        response=self.client.post(self.url,self.post_data(update_source=other.pk))
        self.assertEqual(response.status_code,400)
        self.assertIn('project',response.context['create_form'].errors)
        self.assertEqual(WorkItem.objects.count(),2)
        self.assertEqual(self.client.get(self.url,{'update_project':self.project.pk,'source_request':other.pk}).status_code,404)

    def test_deleted_source_excluded_and_explicit_source_rejected(self):
        WorkItem.objects.filter(pk=self.old.pk).update(deleted_at=timezone.now())
        self.assertIsNone(project_defaults(self.project)['source'])
        with self.assertRaises(ValidationError):initial_for_update(self.project,self.old.pk)

    def test_latest_source_default_but_row_shortcut_uses_selected_source(self):
        data=work_data(project=self.project,kind='update',description='Latest request')
        newer=create_work(data=data,actor=None)[0]
        self.assertEqual(project_defaults(self.project)['source'].pk,newer.pk)
        initial=initial_for_update(self.project,self.old.pk)
        self.assertEqual(initial['description'],self.old.description)
        self.assertEqual(initial['update_source'],self.old.pk)

    def test_shared_email_keeps_selected_company_contact(self):
        client=self.old.client_contact.client
        client.kind='company';client.name='Red Tail';client.save()
        other=client.__class__.objects.create(name='Fed Cap',kind='company')
        ClientContact.objects.create(client=other,email=self.old.contact_email,full_name='Same person')
        response=self.client.post(self.url,self.post_data())
        self.assertEqual(response.status_code,302)
        update=WorkItem.objects.exclude(pk=self.old.pk).get()
        self.assertEqual(update.client_contact.client_id,client.pk)
        self.assertEqual(update.company,'Red Tail')

    def test_new_manual_entry_still_works(self):
        data={'action':'create','new-submission_token':uuid.uuid4(),'new-kind':'new',**{'new-'+k:v for k,v in work_data().items()}}
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(WorkProject.objects.count(),2)

    def test_permissions_protect_customer_details(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.defaults_url).status_code,302)
        user=get_user_model().objects.create_user('customer')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.defaults_url).status_code,302)
        user.is_staff=True;user.save()
        user.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.assertEqual(self.client.get(self.defaults_url).status_code,403)
        self.assertEqual(self.client.get(self.url,{'update_project':self.project.pk}).status_code,403)

    @override_settings(WORK_QUEUE_ENABLED=False,WORK_QUEUE_STAFF_PREVIEW=True)
    def test_preview_and_writes_to_defaults_denied(self):
        self.assertEqual(self.client.get(self.defaults_url).status_code,403)

    def test_defaults_get_only_and_unknown_project(self):
        self.assertEqual(self.client.post(self.defaults_url).status_code,405)
        self.assertEqual(self.client.get(reverse('workqueue:queue_project_defaults',args=[uuid.uuid4()])).status_code,404)

    def test_missing_posted_defaults_loaded_server_side(self):
        response=self.client.post(self.url,{'action':'create','new-project':self.project.pk,'new-submission_token':uuid.uuid4(),'new-description':'New work'})
        self.assertEqual(response.status_code,302)
        update=WorkItem.objects.exclude(pk=self.old.pk).get()
        self.assertEqual(update.project_name,'Lot 12')
        self.assertEqual(update.description,'New work')

    def test_malformed_source_is_form_error(self):
        response=self.client.post(self.url,self.post_data(update_source='broken'))
        self.assertEqual(response.status_code,400)
        self.assertIn('update_source',response.context['create_form'].errors)
