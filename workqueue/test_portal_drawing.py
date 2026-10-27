import json
import uuid
import os
from io import BytesIO
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase,Client as Browser
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from django.urls import reverse
from reportlab.pdfgen.canvas import Canvas
from . import test_portal as fixtures
from . import test_portal_reviews as reviews_fixture
from .portal_models import PortalDocument,PortalMember
from .portal_reviews import clean_payload,save_review,start_review,submit_review
from .portal_services import grant_member,publish_document
from .portal_export_models import PlanReviewExport
from .portal_export_worker import request_export,process_exports,run_parser,FAILURE_MESSAGE
from .models import PendingUpload,NotificationDelivery,WorkItem
from .uploads import private_storage

def shape(kind='arrow',**changes):
    value={'id':str(uuid.uuid4()),'page':1,'x':.25,'y':.25,'text':'Please change this part.',
        'type':kind,'points':[[.25,.25],[.6,.4]]}
    value.update(changes);return value

class DrawingAndExportTests(TestCase):
    setUp=fixtures.PortalTests.setUp
    version=fixtures.PortalTests.version
    sign_in=fixtures.PortalTests.sign_in
    project_url=fixtures.PortalTests.project_url

    def valid_revision(self,content=None):
        if content is None:
            buffer=BytesIO();canvas=Canvas(buffer,pagesize=(612,792));canvas.drawString(40,740,'SAMPLE ORIGINAL PLAN');canvas.rect(100,200,350,400);canvas.save();content=buffer.getvalue()
        nonce=uuid.uuid4();key=private_storage().save('intake/test/'+uuid.uuid4().hex+'.pdf',ContentFile(content))
        upload=PendingUpload.objects.create(intake_nonce=nonce,session_digest='test',original_name='Original plan.pdf',
            storage_key=key,content_type='application/pdf',size_bytes=len(content),state='ready',expires_at=timezone.now()+timedelta(hours=1))
        return publish_document(self.project,version=self.version(),title='Floor plan',kind='plan',billing_client_id=None,
            document_id=None,note='',external_url='',upload_ids=str(upload.pk),nonce=nonce,binding='test',actor=self.owner)

    def submitted(self,content=None,marks=None):
        member=grant_member(self.project,version=self.version(),contact_id=self.contact.pk,financial_access=False,review_access=True,actor=self.owner)
        revision=self.valid_revision(content)
        record=start_review(member,revision,uuid.uuid4())
        data=reviews_fixture.payload(record,annotations=marks if marks is not None else [shape()])
        record,_=submit_review(record.pk,member,data)
        self.sign_in()
        return member,revision,record

    def url(self,record,staff=False):return reverse('workqueue:staff_marked_plan' if staff else 'workqueue:portal_marked_plan',args=[self.project.pk,record.pk])

    def test_old_pin_and_new_shapes_are_backwards_compatible(self):
        for mark in [shape(),shape('box'),shape('freehand'),{key:value for key,value in shape().items() if key not in {'type','points'}}]:
            clean_payload(reviews_fixture.payload(type('Draft',(),{'version':1})(),annotations=[mark]))

    def test_adjusted_marks_round_trip_freeze_and_export_without_changing_source(self):
        from copy import deepcopy
        from pypdf import PdfReader
        member=grant_member(self.project,version=self.version(),contact_id=self.contact.pk,
            financial_access=False,review_access=True,actor=self.owner)
        revision=self.valid_revision()
        record=start_review(member,revision,uuid.uuid4())
        marks=[shape('arrow'),shape('box'),shape('freehand'),
            {key:value for key,value in shape().items() if key not in {'type','points'}}]
        record=save_review(record.pk,member,reviews_fixture.payload(record,annotations=marks))
        adjusted=deepcopy(marks)
        positions=[[[.35,.45],[.75,.8]],[[.15,.3],[.65,.7]],[[.4,.2],[.55,.35],[.8,.6]],[[.6,.6]]]
        for index,mark in enumerate(adjusted):
            mark.update(x=positions[index][0][0],y=positions[index][0][1],text=f'Edited comment {index+1}')
            if 'points' in mark: mark['points']=positions[index]
        record=save_review(record.pk,member,reviews_fixture.payload(record,annotations=adjusted))
        record.refresh_from_db()
        self.assertEqual(record.annotations,adjusted)
        record,_=submit_review(record.pk,member,reviews_fixture.payload(record,annotations=adjusted))
        frozen=deepcopy(record.annotations)
        original=private_storage().open(revision.storage_key,'rb').read()
        request_export(record.pk);self.assertEqual(process_exports(),1)
        export=PlanReviewExport.objects.get(pk=record.pk)
        self.assertEqual(export.state,'ready',export.error)
        marked=PdfReader(BytesIO(private_storage().open(export.storage_key,'rb').read()))
        self.assertIn('Edited comment 4',marked.pages[-1].extract_text())
        # Inspect the actual exported content stream, not a mocked drawing call.
        # Normalized viewer coordinates must map to PDF points (origin at bottom left).
        operations=[(operator,tuple(round(float(value),4) for value in operands))
            for operands,operator in marked.pages[0].get_contents().operations if operator in {b'm',b'l',b're'}]
        def point(value):return (round(value[0]*612,4),round((1-value[1])*792,4))
        self.assertIn((b'm',point(positions[0][0])),operations)
        self.assertIn((b'l',point(positions[0][1])),operations)
        a,b=map(point,positions[1])
        self.assertIn((b're',(a[0],b[1],round(b[0]-a[0],4),round(a[1]-b[1],4))),operations)
        self.assertIn((b'm',point(positions[2][0])),operations)
        for vertex in positions[2][1:]:self.assertIn((b'l',point(vertex)),operations)
        # Number text is centered at each pin, including the moved legacy pin.
        numbers=[]
        marked.pages[0].extract_text(visitor_text=lambda text,cm,tm,font,size:
            numbers.append((text.strip(),float(tm[5]),float(size))) if text.strip() in {'1','2','3','4'} else None)
        for index,vertices in enumerate(positions,1):
            number=next(entry for entry in numbers if entry[0]==str(index))
            self.assertAlmostEqual(number[1],point(vertices[0])[1]-number[2]*.35,places=3)
            x,y=point(vertices[0])
            self.assertTrue(any(operator==b'm' and abs(values[0]-x-number[2])<.001 and abs(values[1]-y)<.001
                for operator,values in operations),'Moved pin circle must be centered at its adjusted x/y')
        self.assertNotIn((b'm',point([.25,.25])),operations,'Old shape anchors must not survive in the overlay')
        self.assertIn('Edited comment 1',record.work_item.description)
        record.refresh_from_db();self.assertEqual(record.annotations,frozen)
        self.assertEqual(private_storage().open(revision.storage_key,'rb').read(),original)

    def test_invalid_shapes_are_rejected_before_saving(self):
        for mark in [shape('script'),shape(points=[]),shape(points=[[.3,.25],[.5,.5]]),shape(points=[[.25,.25],[1.1,.5]]),
                shape(points=[[.25,.25]]*2),shape('freehand',points=[[.25,.25]]*301),shape(unexpected='x'),shape(points=[[.25,.25],[float('nan'),.5]])]:
            with self.assertRaises(ValidationError):clean_payload(reviews_fixture.payload(type('Draft',(),{'version':1})(),annotations=[mark]))

    def test_total_freehand_detail_has_a_bound(self):
        points=[[.25,.25]]+[[.4,.4]]*299
        marks=[shape('freehand',points=points) for _ in range(11)]
        with self.assertRaises(ValidationError):clean_payload(reviews_fixture.payload(type('Draft',(),{'version':1})(),annotations=marks))

    def test_drawings_freeze_in_queue_and_receipts(self):
        _,_,review=self.submitted(marks=[shape('box'),shape('freehand')])
        self.assertEqual(review.annotations[0]['type'],'box')
        self.assertIn('(box)',review.work_item.description)
        for delivery in NotificationDelivery.objects.all():self.assertIn('(freehand)',delivery.body)

    def test_export_request_retries_create_one_job_no_work_or_emails(self):
        _,_,review=self.submitted()
        count=NotificationDelivery.objects.count();jobs=WorkItem.objects.count()
        for _ in range(2):self.assertEqual(self.client.post(self.url(review)).status_code,302)
        self.assertEqual(PlanReviewExport.objects.count(),1)
        self.assertEqual(NotificationDelivery.objects.count(),count);self.assertEqual(WorkItem.objects.count(),jobs)

    def test_get_does_not_generate_export(self):
        _,_,review=self.submitted()
        self.assertEqual(self.client.get(self.url(review)).status_code,302)
        self.assertEqual(PlanReviewExport.objects.count(),0)

    def test_request_is_denied_after_revocation_or_withdrawal(self):
        member,revision,review=self.submitted()
        PortalMember.objects.filter(pk=member.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.client.post(self.url(review)).status_code,404)
        PortalMember.objects.filter(pk=member.pk).update(revoked_at=None)
        PortalDocument.objects.filter(pk=revision.document_id).update(withdrawn_at=timezone.now())
        self.assertEqual(self.client.post(self.url(review)).status_code,404)
        self.assertEqual(PlanReviewExport.objects.count(),0)

    def test_another_email_or_billing_company_cannot_request_export(self):
        member,_,review=self.submitted();self.sign_in(self.other.client_contact.email)
        self.assertEqual(self.client.post(self.url(review)).status_code,404)
        self.sign_in();PortalMember.objects.filter(pk=member.pk).update(client=self.other.client_contact.client)
        self.assertEqual(self.client.post(self.url(review)).status_code,404)

    def test_draft_does_not_offer_an_export(self):
        _,_,review=self.submitted()
        # Build another independent draft against the same immutable release.
        draft=start_review(review.member,review.document_version,uuid.uuid4())
        self.assertEqual(self.client.post(self.url(draft)).status_code,404)

    def test_export_csrf_required(self):
        _,_,review=self.submitted();browser=Browser(enforce_csrf_checks=True);self.sign_in(browser=browser)
        self.assertEqual(browser.post(self.url(review)).status_code,403)

    def test_processor_produces_private_marked_copy_and_leaves_source_untouched(self):
        from pypdf import PdfReader
        _,revision,review=self.submitted(marks=[shape(),shape('box'),shape('freehand')])
        original=private_storage().open(revision.storage_key,'rb').read()
        request_export(review.pk);self.assertEqual(process_exports(),1)
        export=PlanReviewExport.objects.get(pk=review.pk)
        self.assertEqual(export.state,'ready',export.error)
        self.assertTrue(export.storage_key.startswith('intake/review-exports/'))
        marked=private_storage().open(export.storage_key,'rb').read()
        pages=PdfReader(BytesIO(marked)).pages
        self.assertEqual(len(pages),2)
        self.assertIn('SAMPLE ORIGINAL PLAN',pages[0].extract_text())
        self.assertIn('Marked review copy',pages[1].extract_text())
        self.assertIn('Note 3',pages[1].extract_text())
        self.assertEqual(private_storage().open(revision.storage_key,'rb').read(),original)
        response=self.client.get(self.url(review));self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'no-store, private');self.assertEqual(response['Content-Type'],'application/pdf')
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_cached_copy_still_rechecks_current_client_permission(self):
        member,_,review=self.submitted();request_export(review.pk);process_exports()
        PortalMember.objects.filter(pk=member.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url(review)).status_code,404)

    def test_staff_retains_submitted_export_after_withdrawal(self):
        _,revision,review=self.submitted();request_export(review.pk);process_exports()
        PortalDocument.objects.filter(pk=revision.document_id).update(withdrawn_at=timezone.now())
        self.client.force_login(self.owner)
        response=self.client.get(self.url(review,True));self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_malformed_and_encrypted_plans_fail_without_leaking_parser_output(self):
        from pypdf import PdfWriter
        encrypted=BytesIO();writer=PdfWriter();writer.add_blank_page(width=612,height=792);writer.encrypt('private-test-password');writer.write(encrypted)
        for source in [b'%PDF malformed PRIVATE FILE TEXT',encrypted.getvalue()]:
            _,_,review=self.submitted(source);request_export(review.pk);process_exports()
            export=PlanReviewExport.objects.get(pk=review.pk)
            self.assertEqual(export.state,'failed');self.assertEqual(export.error,FAILURE_MESSAGE);self.assertEqual(export.storage_key,'')

    def test_geometry_mismatch_fails_instead_of_misplacing_marks(self):
        _,_,review=self.submitted();review.geometry[0]['width']=800;review.save(update_fields=['geometry'])
        request_export(review.pk);process_exports()
        self.assertEqual(PlanReviewExport.objects.get(pk=review.pk).state,'failed')

    def test_source_size_mismatch_is_rejected_before_parser(self):
        _,revision,review=self.submitted();revision.size_bytes+=1;revision.save(update_fields=['size_bytes'])
        request_export(review.pk)
        with patch('workqueue.portal_export_worker.run_parser') as parser:process_exports();parser.assert_not_called()
        self.assertEqual(PlanReviewExport.objects.get(pk=review.pk).state,'failed')

    def test_timeout_is_held_for_explicit_retry_and_attempts_are_limited(self):
        import subprocess
        _,_,review=self.submitted()
        for _ in range(3):
            request_export(review.pk)
            with patch('workqueue.portal_export_worker.run_parser',side_effect=subprocess.TimeoutExpired('parser',30)):process_exports()
        with self.assertRaises(ValidationError):request_export(review.pk)
        self.assertEqual(PlanReviewExport.objects.get(pk=review.pk).attempts,3)

    def test_interrupted_lease_fails_without_automatic_reprocessing(self):
        _,_,review=self.submitted();export=request_export(review.pk)
        export.state='processing';export.started_at=timezone.now()-timedelta(minutes=6);export.save()
        self.assertEqual(process_exports(),0);export.refresh_from_db();self.assertEqual(export.state,'failed')

    def test_successful_job_is_not_run_twice(self):
        _,_,review=self.submitted();request_export(review.pk);process_exports()
        with patch('workqueue.portal_export_worker.build_export') as build:
            self.assertEqual(process_exports(),0);build.assert_not_called()

    def test_pdf_subprocess_has_timeout_and_no_provider_credentials(self):
        with TemporaryDirectory() as directory,patch.dict(os.environ,{'DATABASE_URL':'PRIVATE','EMAIL_HOST_PASSWORD':'PRIVATE'}),patch('workqueue.portal_export_worker.subprocess.run') as runner:
            run_parser(Path(directory)/'source.pdf',Path(directory)/'notes.json',Path(directory)/'result.pdf')
        options=runner.call_args.kwargs
        self.assertEqual(options['timeout'],30);self.assertNotIn('DATABASE_URL',options['env']);self.assertNotIn('EMAIL_HOST_PASSWORD',options['env'])
        self.assertIn('-I',runner.call_args.args[0]);self.assertNotIn('shell',options)

    def test_rotated_cropped_and_user_unit_pages_keep_the_mark_at_the_viewer_location(self):
        import shutil,subprocess
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import NameObject,NumberObject
        from PIL import Image
        from .portal_export_pdf import export_pdf
        poppler=shutil.which('pdftoppm')
        if not poppler:self.skipTest('Pixel alignment check requires local Poppler.')
        buffer=BytesIO();canvas=Canvas(buffer,pagesize=(400,600))
        for number in range(3):canvas.drawString(55,80,'Original page '+str(number+1));canvas.showPage()
        canvas.save();reader=PdfReader(buffer);writer=PdfWriter()
        for index,page in enumerate(reader.pages):
            page.cropbox.lower_left=(50,70);page.cropbox.upper_right=(350,570)
            page.rotate([0,90,270][index])
            if index==2:page[NameObject('/UserUnit')]=NumberObject(2)
            writer.add_page(page)
        with TemporaryDirectory() as directory:
            source=Path(directory)/'source.pdf';result=Path(directory)/'copy.pdf';writer.write(source)
            geometry=[{'page':1,'width':300,'height':500,'rotation':0},
                {'page':2,'width':500,'height':300,'rotation':90},{'page':3,'width':1000,'height':600,'rotation':270}]
            marks=[shape(page=index+1) for index in range(3)]
            data={'geometry':geometry,'annotations':marks,'title':'Sample','sequence':1,'file':'sample.pdf','project':'Sample',
                'reference':'PHD-TEST','author':'Sample','company':'Sample','email':'test@example.invalid','summary':'Review','submitted':'Test time'}
            export_pdf(source,data,result)
            exported=PdfReader(result)
            self.assertEqual([page.rotation for page in exported.pages[:3]],[0,90,270])
            for page in range(1,4):
                prefix=Path(directory)/('page'+str(page))
                subprocess.run([poppler,'-cropbox','-f',str(page),'-singlefile','-scale-to','600','-png',str(result),str(prefix)],
                    check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                image=Image.open(str(prefix)+'.png').convert('RGB')
                x,y=int(image.width*.25),int(image.height*.25)
                red=sum(1 for i in range(x-12,x+13) for j in range(y-12,y+13)
                    if image.getpixel((i,j))[0]>120 and image.getpixel((i,j))[1]<110 and image.getpixel((i,j))[2]<110)
                self.assertGreater(red,15,f'Page {page}: the number pin must match its normalized viewport location.')

    def test_copy_excludes_embedded_scripts_and_page_link_actions(self):
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,TextStringObject,ArrayObject
        from .portal_export_pdf import export_pdf
        writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
        writer.add_js('alert("not executed")')
        page[NameObject('/AA')]=DictionaryObject({NameObject('/O'):DictionaryObject({NameObject('/S'):NameObject('/JavaScript'),NameObject('/JS'):TextStringObject('not executed')})})
        with TemporaryDirectory() as directory:
            source=Path(directory)/'source.pdf';result=Path(directory)/'copy.pdf';writer.write(source)
            data={'geometry':[],'annotations':[],'title':'Sample','sequence':1,'file':'sample.pdf','project':'Sample',
                'reference':'PHD-TEST','author':'Sample','company':'Sample','email':'test@example.invalid','summary':'Review','submitted':'Test time'}
            export_pdf(source,data,result)
            document=PdfReader(result)
            self.assertNotIn('/Names',document.trailer['/Root']);self.assertNotIn('/AA',document.pages[0])

