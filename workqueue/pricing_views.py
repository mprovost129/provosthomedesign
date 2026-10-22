import uuid
from datetime import timedelta
from django.contrib import messages
from django import forms
from django.http import Http404
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Client,WorkProject
from .pm_views import staff_view,editable,error_text,query_uuid,client_initial
from .pricing import SERVICES,RATE_KEYS,CATEGORIES,latest_card,save_estimate,save_rate_card,create_proposal
from .pricing_models import PricingRateVersion,PricingEstimate,PricingEstimateRevision
from .pricing_forms import PricingForm,RateCardForm,ProposalFromPricingForm
from .services import EditConflict


@staff_view
@require_http_methods(['GET'])
def estimates(request):
    query=request.GET.get('q','').strip()[:120]
    records=PricingEstimate.objects.select_related('client','project','current_revision')
    if query:
        records=records.filter(Q(number__icontains=query)|Q(title__icontains=query)|Q(client__name__icontains=query)|Q(project__name__icontains=query))
    for key in ['client','project']:
        if request.GET.get(key):records=records.filter(**{key+'_id':query_uuid(request.GET[key])})
    filters=request.GET.copy()
    filters.pop('page',None)
    return render(request,'workqueue/pricing_list.html',{'page':Paginator(records,30).get_page(request.GET.get('page')),
        'q':query,'filters':filters.urlencode(),'client_filter':request.GET.get('client',''),
        'project_filter':request.GET.get('project',''),'can_change':request.user.has_perm('workqueue.change_workitem')})


def editor_context(form,result=None):
    clients={str(client.pk):client.pricing_category for client in form.fields['client'].queryset}
    cards={str(card.pk):{'category':card.category,'rates':card.rates,'sequence':card.sequence} for card in PricingRateVersion.objects.all()}
    latest={category:str(card.pk) if (card:=latest_card(category)) else '' for category,_ in CATEGORIES}
    service_rows=[{'key':key,'label':label,'help':help_text,'area':form[key+'_area'],'rate':form[key+'_rate']} for key,label,help_text in SERVICES]
    return {'form':form,'result':result,'service_rows':service_rows,'pricing_config':{'clients':clients,'cards':cards,'latest':latest},
        'can_rates':False}


@staff_view
@require_http_methods(['GET','POST'])
def editor(request,estimate_id=None):
    editable(request)
    estimate=get_object_or_404(PricingEstimate.objects.select_related('current_revision__rate_version'),pk=estimate_id) if estimate_id else None
    initial={'token':estimate.pk if estimate else uuid.uuid4(),'version':estimate.version if estimate else 0,
        'title':'Design pricing estimate','mode':'area','project_review':'standard'}
    if estimate:
        initial.update(estimate.current_revision.snapshot['inputs'],client=estimate.client_id,project=estimate.project_id,
            rate_version=estimate.current_revision.rate_version_id)
    elif request.GET.get('copy'):
        source=get_object_or_404(PricingEstimate.objects.select_related('current_revision'),pk=query_uuid(request.GET['copy']))
        initial.update(source.current_revision.snapshot['inputs'],title='Copy of '+source.title[:190],client=source.client_id,
            project=source.project_id,rate_version=source.current_revision.rate_version_id)
    else:
        client=None
        if request.GET.get('project'):
            project=get_object_or_404(WorkProject,pk=query_uuid(request.GET['project']))
            client=client_initial(project)
            initial.update(project=project.pk,title=project.name)
        if request.GET.get('client'):
            client=get_object_or_404(Client,pk=query_uuid(request.GET['client']),merged_into__isnull=True)
        if client:initial.update(client=client.pk,category=client.pricing_category)
        if request.GET.get('category') in dict(CATEGORIES) and not client:initial['category']=request.GET['category']
        card=latest_card(initial.get('category',''))
        if card:
            initial['rate_version']=card.pk
            initial.update({key+'_rate':value for key,value in card.rates.items()})
    form=PricingForm(request.POST if request.method=='POST' else None,initial=initial)
    result,status=None,200
    if request.method=='POST':
        if form.is_valid():
            result=form.result
            if request.POST.get('action')=='save':
                try:
                    saved=save_estimate(data=form.cleaned_data,token=form.cleaned_data['token'],version=form.cleaned_data['version'],actor=request.user,estimate_id=estimate_id)
                except (ValidationError,EditConflict) as error:
                    form.add_error(None,error_text(error));status=409 if isinstance(error,EditConflict) else 400
                else:
                    messages.success(request,'Pricing calculation saved. Its quantities and rates are preserved in this revision.')
                    return redirect('workqueue:pricing_detail',saved.pk)
            elif request.POST.get('action')!='preview':
                form.add_error(None,'Choose Preview or Save calculation.');status=400
        else:status=400
    context=editor_context(form,result);context.update(estimate=estimate,can_rates=request.user.is_superuser)
    return render(request,'workqueue/pricing_editor.html',context,status=status)


@staff_view
@require_http_methods(['GET','POST'])
def detail(request,estimate_id):
    estimate=get_object_or_404(PricingEstimate.objects.select_related('client','project','current_revision__rate_version','current_revision__proposal'),pk=estimate_id)
    revision=estimate.current_revision
    if request.GET.get('revision'):
        try:
            sequence=forms.IntegerField(min_value=1,max_value=2147483647).clean(request.GET['revision'])
        except ValidationError:
            raise Http404 from None
        revision=get_object_or_404(PricingEstimateRevision.objects.select_related('proposal'),estimate=estimate,sequence=sequence)
    current=revision.pk==estimate.current_revision_id
    snapshot=revision.snapshot
    initial={'version':estimate.version,'title':estimate.title,'scope':'\n'.join(line['description']+' — '+line['quantity'] for line in snapshot['result']['lines']),
        'valid_until':timezone.localdate()+timedelta(days=30)}
    form=ProposalFromPricingForm(request.POST if request.method=='POST' else None,initial=initial)
    status=200
    if request.method=='POST':
        editable(request)
        if not current:raise PermissionDenied
        if form.is_valid():
            try:
                proposal=create_proposal(estimate_id=estimate.pk,version=form.cleaned_data['version'],data=form.cleaned_data,actor=request.user)
            except (ValidationError,EditConflict) as error:
                form.add_error(None,error_text(error));status=409 if isinstance(error,EditConflict) else 400
            else:
                messages.success(request,'Proposal draft prepared. Review it before sending. No client email or invoice was created.')
                return redirect('workqueue:proposal_detail',proposal.pk)
        else:status=400
    return render(request,'workqueue/pricing_detail.html',{'estimate':estimate,'revision':revision,'snapshot':snapshot,
        'result':snapshot['result'],'proposal_form':form,'current':current,'can_change':request.user.has_perm('workqueue.change_workitem')},status=status)


@staff_view
@require_http_methods(['GET','POST'])
def rates(request):
    editable(request)
    if not request.user.is_superuser:raise PermissionDenied
    category=request.POST.get('category',request.GET.get('category','contractor'))
    if category not in dict(CATEGORIES):category='contractor'
    card=latest_card(category)
    initial={'category':category,'expected':card.sequence if card else 0,'label':card.label if card else dict(CATEGORIES)[category]+' pricing'}
    if card:initial.update(card.rates,source_note=card.source_note)
    form=RateCardForm(request.POST if request.method=='POST' else None,initial=initial);status=200
    if request.method=='POST':
        if form.is_valid():
            try:
                save_rate_card(category=category,label=form.cleaned_data['label'],source_note=form.cleaned_data['source_note'],
                    rates={key:form.cleaned_data[key] for key in RATE_KEYS},expected=form.cleaned_data['expected'],actor=request.user)
            except (ValidationError,EditConflict) as error:
                form.add_error(None,error_text(error));status=409 if isinstance(error,EditConflict) else 400
            else:
                messages.success(request,'A new rate-card version was saved. Existing calculations and proposals keep their saved prices.')
                return redirect(reverse('workqueue:pricing_rates')+'?category='+category)
        else:status=400
    return render(request,'workqueue/pricing_rates.html',{'form':form,'card':card,'category':category,'categories':CATEGORIES},status=status)
