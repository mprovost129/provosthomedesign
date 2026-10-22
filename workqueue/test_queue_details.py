import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, Client as Browser, override_settings
from django.urls import reverse
from .forms import QueueEditForm
from .models import AuditEvent, NotificationDelivery, ProjectAccess
from .services import create_work, edit_work
from .tests import work_data


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True, BOOKING_ENABLED=False)
class QueueDetailEditTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('detail-owner','owner@example.invalid','test')
        self.item=create_work(data=work_data(),actor=self.owner)[0]
        self.client.force_login(self.owner)
        self.url=reverse('workqueue:queue')

    def post_data(self,**changes):
        form=QueueEditForm(instance=self.item,prefix=str(self.item.pk))
        data={'action':'edit','item_id':str(self.item.pk)}
        for field in form:
            value=field.value()
            data[field.html_name]='' if value is None else value
        data.update({f'{self.item.pk}-{key}':value for key,value in changes.items()})
        return data

    def test_full_queue_correction_preserves_original_submission_crm_and_access(self):
        original=self.item.submissions.get();answers=original.answers.copy();owner_email=original.owner_email
        contact=self.item.client_contact;original_contact=(contact.full_name,contact.email,contact.phone)
        client=contact.client;original_billing=(client.billing_street,client.billing_city)
        grants=list(ProjectAccess.objects.values_list('pk','email'))
        response=self.client.post(self.url,self.post_data(project_name='Corrected lot 12',kind='update',
            contact_full_name='Corrected contact',company='Corrected company',contact_email='CORRECTED@example.invalid',
            contact_phone='508-555-0199',billing_street='12 Test Lane',billing_city='Boston',billing_state='MA',
            billing_zip='02108',project_context='Corrected reference',source='Phone correction',legacy_ref='Old-12',
            description='Corrected requested work',service_needed='Renovation',internal_notes='Private correction'))
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db();self.assertEqual(self.item.project.name,'Corrected lot 12')
        self.assertEqual(self.item.contact_email,'corrected@example.invalid');self.assertEqual(self.item.billing_street,'12 Test Lane')
        self.assertEqual(self.item.kind,'update');self.assertEqual(self.item.project_context,'Corrected reference')
        self.assertEqual(self.item.source,'Phone correction');self.assertEqual(self.item.legacy_ref,'Old-12')
        self.assertEqual(self.item.description,'Corrected requested work');self.assertEqual(self.item.queue_order,1)
        original.refresh_from_db();self.assertEqual(original.answers,answers);self.assertEqual(original.owner_email,owner_email)
        contact.refresh_from_db();client.refresh_from_db()
        self.assertEqual((contact.full_name,contact.email,contact.phone),original_contact)
        self.assertEqual((client.billing_street,client.billing_city),original_billing)
        self.assertEqual(list(ProjectAccess.objects.values_list('pk','email')),grants)
        self.assertFalse(NotificationDelivery.objects.exists())
        audit=AuditEvent.objects.filter(work_item=self.item,action='edited').latest('pk')
        self.assertEqual(audit.changes['contact_email']['after'],'corrected@example.invalid')
        self.assertIn('project_details',audit.changes)

    def test_invalid_contact_or_dates_never_partially_rename_a_shared_project(self):
        before=self.item.project.name
        response=self.client.post(self.url,self.post_data(contact_email='invalid-email',project_name='Do not save'))
        self.assertEqual(response.status_code,400)
        self.item.refresh_from_db();self.assertEqual(self.item.project.name,before)
        self.assertEqual(self.item.version,1)
        with self.assertRaises(ValidationError):
            edit_work(item_id=self.item.pk,version=1,data={'project_name':'Do not save','contact_email':'invalid-email'},actor=self.owner)
        self.item.refresh_from_db();self.assertEqual(self.item.project.name,before)
        self.assertEqual(self.item.version,1)

    def test_blank_imported_contact_fields_remain_editable(self):
        response=self.client.post(self.url,self.post_data(contact_full_name='',company='',contact_email='',contact_phone='',
            billing_street='',description='Fill in missing details later'))
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db();self.assertEqual(self.item.contact_email,'')
        self.assertEqual(self.item.description,'Fill in missing details later')

    def test_edit_details_is_explicit_and_permissions_and_csrf_still_apply(self):
        response=self.client.get(self.url)
        self.assertContains(response,'Edit details')
        self.assertContains(response,f'id="edit-{self.item.pk}"')
        self.assertContains(response,'Contact and billing for this request')
        browser=Browser(enforce_csrf_checks=True);browser.force_login(self.owner)
        self.assertEqual(browser.post(self.url,self.post_data()).status_code,403)
        reader=get_user_model().objects.create_user('detail-reader',is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(reader)
        self.assertNotContains(self.client.get(self.url),'data-queue-editor')
        self.assertEqual(self.client.post(self.url,self.post_data()).status_code,403)

    def test_incomplete_project_uses_provided_address_and_corrections_reach_linked_rows(self):
        sibling=create_work(data=work_data(kind='update',project=self.item.project),actor=self.owner)[0]
        original_answers=sibling.submissions.get().answers.copy()
        project=self.item.project;project.street='';project.save(update_fields=['street'])
        form=QueueEditForm(instance=self.item,prefix=str(self.item.pk))
        self.assertEqual(form['project_street'].value(),'7 Park St Unit 1')
        response=self.client.post(self.url,self.post_data(project_name='Corrected lot 12'))
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db();sibling.refresh_from_db()
        self.assertEqual(self.item.project.street,'7 Park St Unit 1')
        self.assertEqual(sibling.project_name,'Corrected lot 12')
        self.assertEqual(sibling.project_street,'7 Park St Unit 1')
        self.assertEqual(sibling.submissions.get().answers,original_answers)
