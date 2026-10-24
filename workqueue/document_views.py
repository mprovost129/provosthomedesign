import uuid
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Client, WorkProject
from .pm_models import Proposal
from .pm_forms import LineFormSet
from .pm_views import staff_view, editable, query_uuid, error_text, client_initial
from .pm_services import current_client
from .document_models import InvoiceDocument
from .document_forms import InvoiceDocumentForm, invoice_form_data
from .document_content import SECTIONS, checked_rich, client_projection, project_projection, issuer
from .document_services import save_invoice_document
from .services import EditConflict


def related_projects(client):
    if not client:
        return WorkProject.objects.none()
    return WorkProject.objects.filter(Q(work_items__client_contact__client=client) | Q(client_links__client=client)).distinct().order_by('name')


class StartForm(forms.Form):
    kind = forms.ChoiceField(choices=[('estimate','Estimate'),('proposal','Proposal'),('invoice','Invoice draft')], label='Document type')
    client = forms.ModelChoiceField(queryset=Client.objects.none(), label='Client')
    project = forms.ModelChoiceField(queryset=WorkProject.objects.none(), label='Project')

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.filter(merged_into__isnull=True).order_by('name')
        self.fields['project'].queryset = related_projects(client)


def editor_context(form, project, *, invoice=False):
    clients = [{'id':str(client.pk), 'url':reverse('workqueue:client_detail',args=[client.pk]), **client_projection(client)} for client in form.fields['client'].queryset]
    values = {key:form[key].value() or '' for key,_ in SECTIONS}
    values['rich_sections'] = {key:form[key+'_html'].value() or '' for key,_ in SECTIONS}
    try:
        rich = checked_rich(values)
    except ValidationError:
        rich = {}  # The bound form displays its length errors; never fail the editor.
    return {'document_clients':clients, 'document_project':project_projection(project), 'document_issuer':issuer(),
        'document_meta':[form[name] for name in ('title','client','document_kind','pricing','issue_date','due_date' if invoice else 'valid_until','document_reference')],
        'document_sections':[form[key] for key,_ in SECTIONS if not form.fields[key].widget.is_hidden],
        'editor_kind':'invoice' if invoice else 'proposal', 'document_rich':rich}


@staff_view
@require_http_methods(['GET'])
def documents(request):
    q = request.GET.get('q','').strip()[:150]
    client = get_object_or_404(Client, pk=query_uuid(request.GET['client']), merged_into__isnull=True) if request.GET.get('client') else None
    project_id = query_uuid(request.GET['project']) if request.GET.get('project') else None
    kind = request.GET.get('kind','proposal')
    if kind not in {'estimate','proposal','invoice'}:
        kind='proposal'
    initial={'kind':kind,'client':client,'project':project_id}
    start=StartForm(request.GET if request.GET.get('action')=='start' else None,client=client,initial=initial)
    if request.GET.get('action')=='start' and start.is_valid():
        destination='workqueue:invoice_document_new' if kind=='invoice' else 'workqueue:proposal_new'
        return redirect(reverse(destination,args=[start.cleaned_data['project'].pk])+f'?client={start.cleaned_data["client"].pk}&kind={kind}')
    proposals=Proposal.objects.select_related('client','project','current_revision').order_by('-updated_at')
    invoices=InvoiceDocument.objects.select_related('client','project').order_by('-updated_at')
    if client:
        proposals=proposals.filter(client=client);invoices=invoices.filter(client=client)
    if project_id:
        proposals=proposals.filter(project_id=project_id);invoices=invoices.filter(project_id=project_id)
    if q:
        where=Q(title__icontains=q)|Q(project__name__icontains=q)|Q(client__name__icontains=q)
        proposals=proposals.filter(where|Q(number__icontains=q));invoices=invoices.filter(where)
    rows=[{'number':p.number,'title':p.title,'client':p.client,'project':p.project,'kind':p.document_label,
        'status':p.status_label,'total':(p.draft if p.has_draft else p.current_revision.snapshot).get('total',''),
        'updated_at':p.updated_at,'url':reverse('workqueue:proposal_detail',args=[p.pk])} for p in proposals[:200]]
    rows.extend({'number':d.number,'title':d.title,'client':d.client,'project':d.project,'kind':'Invoice draft',
        'status':'Working draft · not issued','total':d.draft.get('total',''),'updated_at':d.updated_at,
        'url':reverse('workqueue:invoice_document_detail',args=[d.pk])} for d in invoices[:200])
    rows.sort(key=lambda row:row['updated_at'],reverse=True)
    return render(request,'workqueue/documents.html',{'start_form':start,'q':q,'selected_client':client,'project_filter':project_id,
        'page':Paginator(rows,25).get_page(request.GET.get('page'))})


@staff_view
@require_http_methods(['GET','POST'])
def invoice_editor(request,project_id,document_id=None):
    editable(request)
    project=get_object_or_404(WorkProject,pk=project_id)
    document=get_object_or_404(InvoiceDocument,pk=document_id,project=project) if document_id else None
    initial=dict(document.draft) if document else {'title':project.name,'document_kind':'invoice','pricing':'fixed',
        'issue_date':timezone.localdate(),'due_date':timezone.localdate()+timedelta(days=30)}
    if not document and request.GET.get('proposal'):
        proposal=get_object_or_404(Proposal.objects.select_related('current_revision','client'),pk=query_uuid(request.GET['proposal']),project=project,
            has_draft=False,current_revision__state='accepted')
        initial.update({key:proposal.current_revision.snapshot.get(key,'') for key in ('title','lines','terms','notes','rich_sections')})
        initial['client']=current_client(proposal.client)
        initial['source_revision']=proposal.current_revision_id
    if document:
        initial['source_revision']=document.source_revision_id
    initial.update(client=current_client(document.client) if document else initial.get('client') or (query_uuid(request.GET['client']) if request.GET.get('client') else client_initial(project)),
        token=document.pk if document else uuid.uuid4(),version=document.version if document else 0)
    form=InvoiceDocumentForm(request.POST or None,project=project,proposal=document,initial=initial)
    lines=LineFormSet(request.POST or None,prefix='lines',initial=initial.get('lines') or [{'description':'','quantity':'1','rate':''}])
    status=200
    if request.method=='POST':
        status=400
        valid=form.is_valid();valid_lines=lines.is_valid()
        if valid and valid_lines:
            try:
                document=save_invoice_document(project_id=project.pk,client_id=form.cleaned_data['client'].pk,
                    data=invoice_form_data(form,lines),token=form.cleaned_data['token'],version=form.cleaned_data['version'],actor=request.user,
                    document_id=document.pk if document else None)
            except (ValidationError,EditConflict) as exc:
                form.add_error(None,error_text(exc));status=409 if isinstance(exc,EditConflict) else 400
            else:
                messages.success(request,'Invoice working draft saved. It has not been issued or sent.')
                return redirect('workqueue:invoice_document_detail',document.pk)
    return render(request,'workqueue/document_editor.html',{'form':form,'lines':lines,'project':project,'invoice_document':document,
        **editor_context(form,project,invoice=True)},status=status)


@staff_view
@require_http_methods(['GET'])
def invoice_detail(request,document_id):
    document=get_object_or_404(InvoiceDocument.objects.select_related('client','project'),pk=document_id)
    return render(request,'workqueue/invoice_document_detail.html',{'document':document,
        'snapshot':document.revisions.first().snapshot,'history':document.revisions.all()[:20]})


@staff_view
@require_http_methods(['GET'])
def invoice_pdf(request,document_id):
    document=get_object_or_404(InvoiceDocument,pk=document_id)
    if request.GET.get('revision'):
        try:
            sequence=forms.IntegerField(min_value=1,max_value=1000000).clean(request.GET['revision'])
        except ValidationError:
            from django.http import Http404
            raise Http404 from None
        revision=document.revisions.filter(sequence=sequence).first()
    else:
        revision=document.revisions.first()
    if not revision:
        from django.http import Http404
        raise Http404
    from .document_pdf import document_pdf
    response=HttpResponse(document_pdf(revision.snapshot,invoice_draft=True),content_type='application/pdf')
    response['Content-Disposition']=f'attachment; filename="{document.number}-working-draft-v{revision.sequence}.pdf"'
    return response
