from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .models import Client, ClientContact
from .crm import checked_client, changed, require_staff
from .services import lock_queue, EditConflict
from .pm_views import staff_view, editable, error_text

SALT='workqueue.shared-contact.v1'


class LinkForm(forms.Form):
    company=forms.ModelChoiceField(queryset=Client.objects.none(),label='Other existing company')
    signature=forms.CharField(widget=forms.HiddenInput)
    confirm=forms.BooleanField(label='Connect this person to the selected company; keep both companies and their work separate')
    def __init__(self,*args,source,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['company'].queryset=Client.objects.filter(kind='company',merged_into__isnull=True).exclude(pk=source.pk)


@transaction.atomic
def share_contact(source_id,contact_id,target_id,source_version,target_version,actor):
    require_staff(actor,'workqueue.change_workitem');lock_queue()
    source=checked_client(source_id,source_version);target=checked_client(target_id,target_version)
    if source.pk==target.pk or target.kind!='company':raise ValidationError('Choose a different company.')
    contact=source.contacts.filter(pk=contact_id,archived_at__isnull=True).first()
    if not contact:raise EditConflict('The source contact changed. Reload before linking it.')
    root=contact.linked_contact_id or contact.pk
    existing=target.contacts.filter(email=contact.email).first() if contact.email else None
    existing=existing or target.contacts.filter(linked_contact_id=root,archived_at__isnull=True).first()
    if existing:
        if (existing.linked_contact_id or existing.pk)!=root:
            if existing.linked_contact_id:
                raise ValidationError('This contact is already connected to a different person. Review the existing connection first.')
            existing.linked_contact_id=root;existing.save(update_fields=['linked_contact'])
            changed(source,actor,'Contact linked to another company',{'contact':str(contact.pk),'company':str(target.pk),'name':target.name})
            changed(target,actor,'Existing person connected',{'contact':str(existing.pk),'source_contact':str(contact.pk),'company':str(source.pk)})
        return existing
    linked=ClientContact(client=target,linked_contact_id=root,email=contact.email,full_name=contact.full_name,
        phone=contact.phone,role=contact.role,submitted_company=target.name,normalized_name=contact.normalized_name,
        normalized_phone=contact.normalized_phone,normalized_company=target.normalized_name)
    linked.full_clean();linked.save()
    changed(source,actor,'Contact linked to another company',{'contact':str(contact.pk),'company':str(target.pk),'name':target.name})
    changed(target,actor,'Existing person connected',{'contact':str(linked.pk),'source_contact':str(contact.pk),'company':str(source.pk)})
    return linked


@staff_view
@require_http_methods(['GET','POST'])
def link_company(request,client_id,contact_id):
    editable(request)
    source=get_object_or_404(Client,pk=client_id,merged_into__isnull=True)
    contact=get_object_or_404(ClientContact,pk=contact_id,client=source,archived_at__isnull=True)
    versions={str(c.pk):c.version for c in Client.objects.filter(kind='company',merged_into__isnull=True).exclude(pk=source.pk)}
    signature=signing.dumps({'source':str(source.pk),'contact':str(contact.pk),'version':source.version,'targets':versions},salt=SALT)
    form=LinkForm(request.POST or None,source=source,initial={'signature':signature});status=200
    if request.method=='POST':
        status=400
        if form.is_valid():
            try:
                signed=signing.loads(form.cleaned_data['signature'],salt=SALT,max_age=3600)
                if signed['source']!=str(source.pk) or signed['contact']!=str(contact.pk):raise ValidationError('Reload this contact before linking it.')
                target=form.cleaned_data['company']
                if str(target.pk) not in signed['targets']:raise ValidationError('Choose a company from this preview.')
                share_contact(source.pk,contact.pk,target.pk,signed['version'],signed['targets'][str(target.pk)],request.user)
            except (signing.BadSignature,KeyError):form.add_error(None,'This preview expired. Reload before linking the contact.')
            except (ValidationError,EditConflict) as exc:form.add_error(None,error_text(exc))
            else:
                messages.success(request,'Contact connected. Company records, jobs, invoices and client access remain separate.')
                return redirect('workqueue:client_detail',target.pk)
    return render(request,'workqueue/shared_contact.html',{'source':source,'contact':contact,'form':form},status=status)
