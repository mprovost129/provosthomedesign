"""Bounded, read-only search of saved staff records. Never calls providers."""
from functools import reduce
from operator import or_
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.db.models import Q, Exists, OuterRef
from django.db.models.functions import Lower, Trim
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .models import Client, ClientContact, WorkItem, WorkProject
from .pm_models import Proposal
from .freshbooks_models import FreshBooksInvoice
from .invoice_history_models import HistoricalInvoice
from .pm_views import staff_view


def matching(queryset, tokens, fields):
    for token in tokens:
        queryset = queryset.filter(reduce(or_, (Q(**{field+'__icontains': token}) for field in fields)))
    return queryset.distinct()


def saved_records(tokens):
    clients = Client.objects.filter(merged_into__isnull=True)
    for token in tokens:
        contacts = matching(ClientContact.objects.filter(client_id=OuterRef('pk'), archived_at__isnull=True), [token], ['full_name','email','phone'])
        clients = clients.alias(contact_match=Exists(contacts)).filter(Q(name__icontains=token) | Q(contact_match=True))
    projects = matching(WorkProject.objects.select_related('filing_folder'), tokens,
        ['name','street','city','canonical_reference','filing_folder__number','work_items__reference','work_items__client_contact__client__name'])
    requests = matching(WorkItem.objects.select_related('project','client_contact__client'), tokens,
        ['reference','project_name','project_street','project__name','project__filing_folder__number','contact_full_name','company','contact_email','client_contact__client__name'])
    proposals = matching(Proposal.objects.select_related('project','client','current_revision').defer('draft'), tokens,
        ['number','title','project__name','project__street','client__name','project__filing_folder__number'])
    invoices = matching(FreshBooksInvoice.objects.exclude(state='released').select_related('proposal__project','proposal__client','project','client','time_batch').defer('payload'), tokens,
        ['invoice_number','proposal__number','proposal__title','proposal__project__name','proposal__client__name','project__name','client__name'])
    # Prefer the connected record when imported history refers to that invoice.
    linked = FreshBooksInvoice.objects.filter(account_id=OuterRef('account_id')).annotate(number_key=Lower(Trim('invoice_number'))).filter(
        Q(number_key=OuterRef('number_key')) & ~Q(number_key='') |
        Q(invoice_id=OuterRef('live_balance__provider_invoice_id'), invoice_id__isnull=False))
    history = HistoricalInvoice.objects.filter(removed_at__isnull=True).annotate(number_key=Lower(Trim('number'))).alias(
        connected=Exists(linked)).filter(connected=False).select_related('client','project').defer('lines')
    history = matching(history, tokens, ['number','exported_client','client__name','project__name','project__street'])
    return [('clients','Clients',clients.order_by('name','pk')),
        ('projects','Projects',projects.order_by('name','pk')),
        ('requests','Work requests',requests.order_by('-received_at','pk')),
        ('proposals','Proposals',proposals.order_by('-updated_at','pk')),
        ('invoices','Connected invoices',invoices.order_by('-created_at','pk')),
        ('history','Imported invoices',history.order_by('-issued','pk'))]


def result_row(kind, record):
    if kind == 'clients':
        return {'title':record.name,'detail':record.get_kind_display(),'url':reverse('workqueue:client_detail',args=[record.pk])}
    if kind == 'projects':
        folder = getattr(record,'filing_folder',None)
        return {'title':f'{folder.number if folder and folder.number else record.canonical_reference or "Unnumbered"} · {record.name}',
            'detail':', '.join(filter(None,[record.street,record.city,record.state])), 'url':reverse('workqueue:project_detail',args=[record.pk])}
    if kind == 'requests':
        return {'title':f'{record.reference} · {record.project.name if record.project_id else record.project_name or "Unlinked project"}',
            'detail':f'{record.contact_full_name} · {record.get_status_display()}',
            'url':reverse('workqueue:queue')+'?'+urlencode({'scope':'all','q':record.reference})+'#request-'+str(record.pk)}
    if kind == 'proposals':
        return {'title':f'{record.number} · {record.title}','detail':f'{record.client.name} · {record.status_label}',
            'url':reverse('workqueue:proposal_detail',args=[record.pk])}
    if kind == 'invoices':
        client = record.proposal.client if record.proposal_id else record.client
        batch = getattr(record,'time_batch',None)
        url = reverse('workqueue:proposal_detail',args=[record.proposal_id]) if record.proposal_id else (
            reverse('workqueue:time_bill_detail',args=[batch.pk]) if batch else reverse('workqueue:invoice_overview')+'?'+urlencode({'q':record.invoice_number}))
        return {'title':record.invoice_number or 'Preparing invoice','detail':f'{client.name if client else "Client not linked"} · {record.billing_status or record.get_state_display()}', 'url':url}
    return {'title':record.number,'detail':f'{record.client.name if record.client_id else record.exported_client} · Imported history',
        'url':reverse('workqueue:invoice_history_detail',args=[record.pk])}


@staff_view
@require_GET
def search(request):
    query = ' '.join(request.GET.get('q','').split())[:120]
    kind = request.GET.get('type','all')
    allowed = {'all','clients','projects','requests','proposals','invoices','history'}
    if kind not in allowed:kind='all'
    groups=[]
    if len(query)>=2:
        for key,label,records in saved_records(query.split()[:6]):
            if kind!='all' and kind!=key:continue
            page=Paginator(records,6 if kind=='all' else 30).get_page(1 if kind=='all' else request.GET.get('page'))
            groups.append({'key':key,'label':label,'count':page.paginator.count,'page':page,
                'rows':[result_row(key,record) for record in page],
                'more_url':reverse('workqueue:staff_search')+'?'+urlencode({'q':query,'type':key})})
    response=render(request,'workqueue/staff_search.html',{'q':query,'kind':kind,'groups':groups,
        'result_count':sum(group['count'] for group in groups),'page_query':urlencode({'q':query,'type':kind})})
    response['Referrer-Policy']='same-origin'
    response['X-Robots-Tag']='noindex, nofollow'
    return response
