from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Q
from .models import Plans, HouseStyle, PlanGallery
from .portal_forms import PlanForm, PlanGalleryFormSet, HouseStyleForm


@staff_member_required(login_url='/portal/login/')
def portal_plan_list(request):
    """List all plans in the employee portal."""
    search_query = request.GET.get('q', '')
    style_filter = request.GET.get('style', '')
    status_filter = request.GET.get('status', '')
    
    plans = Plans.objects.all().prefetch_related('house_styles')
    
    # Apply filters
    if search_query:
        plans = plans.filter(
            Q(plan_number__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(sku__icontains=search_query)
        )
    
    if style_filter:
        plans = plans.filter(house_styles__slug=style_filter)
    
    if status_filter == 'available':
        plans = plans.filter(is_available=True)
    elif status_filter == 'unavailable':
        plans = plans.filter(is_available=False)
    elif status_filter == 'featured':
        plans = plans.filter(is_featured=True)
    
    # Get styles for filter dropdown
    styles = HouseStyle.objects.all()
    
    context = {
        'plans': plans,
        'styles': styles,
        'search_query': search_query,
        'style_filter': style_filter,
        'status_filter': status_filter,
    }
    
    return render(request, 'plans/portal/plan_list.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_plan_create(request):
    """Create a new plan."""
    if request.method == 'POST':
        form = PlanForm(request.POST, request.FILES)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f'Plan {plan.plan_number} created successfully!')
            return redirect('plans:portal_plan_detail', pk=plan.pk)
    else:
        form = PlanForm()
    
    context = {
        'form': form,
        'title': 'Create New Plan',
        'button_text': 'Create Plan',
    }
    
    return render(request, 'plans/portal/plan_form.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_plan_detail(request, pk):
    """View plan details with gallery images."""
    plan = get_object_or_404(Plans.objects.prefetch_related('house_styles', 'images'), pk=pk)
    
    context = {
        'plan': plan,
        'gallery_images': plan.images.all(),
    }
    
    return render(request, 'plans/portal/plan_detail.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_plan_edit(request, pk):
    """Edit an existing plan."""
    plan = get_object_or_404(Plans, pk=pk)
    
    if request.method == 'POST':
        form = PlanForm(request.POST, request.FILES, instance=plan)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f'Plan {plan.plan_number} updated successfully!')
            return redirect('plans:portal_plan_detail', pk=plan.pk)
    else:
        form = PlanForm(instance=plan)
    
    context = {
        'form': form,
        'plan': plan,
        'title': f'Edit Plan {plan.plan_number}',
        'button_text': 'Update Plan',
    }
    
    return render(request, 'plans/portal/plan_form.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_plan_gallery(request, pk):
    """Manage gallery images for a plan."""
    plan = get_object_or_404(Plans, pk=pk)
    
    if request.method == 'POST':
        formset = PlanGalleryFormSet(request.POST, request.FILES, instance=plan)
        if formset.is_valid():
            formset.save()
            messages.success(request, 'Gallery images updated successfully!')
            return redirect('plans:portal_plan_detail', pk=plan.pk)
    else:
        formset = PlanGalleryFormSet(instance=plan)
    
    context = {
        'plan': plan,
        'formset': formset,
    }
    
    return render(request, 'plans/portal/plan_gallery.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_plan_delete(request, pk):
    """Delete a plan."""
    plan = get_object_or_404(Plans, pk=pk)
    
    if request.method == 'POST':
        plan_number = plan.plan_number
        plan.delete()
        messages.success(request, f'Plan {plan_number} deleted successfully!')
        return redirect('plans:portal_plan_list')
    
    context = {
        'plan': plan,
    }
    
    return render(request, 'plans/portal/plan_confirm_delete.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_style_list(request):
    """List all house styles."""
    styles = HouseStyle.objects.all()
    
    context = {
        'styles': styles,
    }
    
    return render(request, 'plans/portal/style_list.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_style_create(request):
    """Create a new house style."""
    if request.method == 'POST':
        form = HouseStyleForm(request.POST, request.FILES)
        if form.is_valid():
            style = form.save()
            messages.success(request, f'House style "{style.style_name}" created successfully!')
            return redirect('plans:portal_style_list')
    else:
        form = HouseStyleForm()
    
    context = {
        'form': form,
        'title': 'Create New House Style',
        'button_text': 'Create Style',
    }
    
    return render(request, 'plans/portal/style_form.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_style_edit(request, pk):
    """Edit an existing house style."""
    style = get_object_or_404(HouseStyle, pk=pk)
    
    if request.method == 'POST':
        form = HouseStyleForm(request.POST, request.FILES, instance=style)
        if form.is_valid():
            style = form.save()
            messages.success(request, f'House style "{style.style_name}" updated successfully!')
            return redirect('plans:portal_style_list')
    else:
        form = HouseStyleForm(instance=style)
    
    context = {
        'form': form,
        'style': style,
        'title': f'Edit Style: {style.style_name}',
        'button_text': 'Update Style',
    }
    
    return render(request, 'plans/portal/style_form.html', context)


@staff_member_required(login_url='/portal/login/')
def portal_style_delete(request, pk):
    """Delete a house style."""
    style = get_object_or_404(HouseStyle, pk=pk)
    
    if request.method == 'POST':
        style_name = style.style_name
        style.delete()
        messages.success(request, f'House style "{style_name}" deleted successfully!')
        return redirect('plans:portal_style_list')
    
    context = {
        'style': style,
    }
    
    return render(request, 'plans/portal/style_confirm_delete.html', context)
