from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import HelpCategory, HelpArticle, FAQ


@login_required(login_url='/portal/login/')
def help_center(request):
    """Main help center page."""
    user = request.user
    is_staff = user.is_staff
    
    # Determine audience filter
    if is_staff:
        audience_filter = Q(audience__in=['staff', 'both'])
    else:
        audience_filter = Q(audience__in=['client', 'both'])
    
    # Get all active categories (don't filter by audience - show all categories that have relevant articles)
    categories = HelpCategory.objects.filter(
        is_active=True
    ).prefetch_related('articles', 'faqs').order_by('order')
    
    # Get featured articles
    featured_articles = HelpArticle.objects.filter(
        is_active=True,
        is_featured=True
    ).filter(audience_filter).select_related('category')[:6]
    
    # Get popular articles (most viewed)
    popular_articles = HelpArticle.objects.filter(
        is_active=True
    ).filter(audience_filter).select_related('category').order_by('-views')[:5]
    
    # Get recent articles
    recent_articles = HelpArticle.objects.filter(
        is_active=True
    ).filter(audience_filter).select_related('category').order_by('-created_at')[:5]
    
    # Search query
    search_query = request.GET.get('q', '')
    search_results = None
    if search_query:
        search_results = HelpArticle.objects.filter(
            Q(title__icontains=search_query) |
            Q(summary__icontains=search_query) |
            Q(content__icontains=search_query),
            is_active=True
        ).filter(audience_filter).select_related('category')[:10]
    
    # Filter categories to only show those with articles for this audience
    categories_with_articles = []
    for category in categories:
        article_count = category.articles.filter(is_active=True).filter(audience_filter).count()
        if article_count > 0:
            category.filtered_article_count = article_count
            categories_with_articles.append(category)
    
    context = {
        'categories': categories_with_articles,
        'featured_articles': featured_articles,
        'popular_articles': popular_articles,
        'recent_articles': recent_articles,
        'search_query': search_query,
        'search_results': search_results,
        'is_staff': is_staff,
    }
    
    return render(request, 'help/help_center.html', context)


@login_required(login_url='/portal/login/')
def category_detail(request, slug):
    """View articles in a specific category."""
    user = request.user
    is_staff = user.is_staff
    
    # Determine audience filter
    if is_staff:
        audience_filter = Q(audience__in=['staff', 'both'])
    else:
        audience_filter = Q(audience__in=['client', 'both'])
    
    category = get_object_or_404(
        HelpCategory.objects.filter(is_active=True).filter(audience_filter),
        slug=slug
    )
    
    # Get articles in this category
    articles = HelpArticle.objects.filter(
        category=category,
        is_active=True
    ).filter(audience_filter)
    
    # Get FAQs in this category
    faqs = FAQ.objects.filter(
        category=category,
        is_active=True
    ).filter(audience_filter)
    
    context = {
        'category': category,
        'articles': articles,
        'faqs': faqs,
        'is_staff': is_staff,
    }
    
    return render(request, 'help/category_detail.html', context)


@login_required(login_url='/portal/login/')
def article_detail(request, slug):
    """View a specific help article."""
    user = request.user
    is_staff = user.is_staff
    
    # Determine audience filter
    if is_staff:
        audience_filter = Q(audience__in=['staff', 'both'])
    else:
        audience_filter = Q(audience__in=['client', 'both'])
    
    article = get_object_or_404(
        HelpArticle.objects.filter(is_active=True).filter(audience_filter).select_related('category'),
        slug=slug
    )
    
    # Increment view count
    article.increment_views()
    
    # Get related articles (same category)
    related_articles = HelpArticle.objects.filter(
        category=article.category,
        is_active=True
    ).filter(audience_filter).exclude(pk=article.pk)[:3]
    
    context = {
        'article': article,
        'related_articles': related_articles,
        'is_staff': is_staff,
    }
    
    return render(request, 'help/article_detail.html', context)


@login_required(login_url='/portal/login/')
def faq_list(request):
    """View all FAQs."""
    user = request.user
    is_staff = user.is_staff
    
    # Determine audience filter
    if is_staff:
        audience_filter = Q(audience__in=['staff', 'both'])
    else:
        audience_filter = Q(audience__in=['client', 'both'])
    
    # Get all FAQs grouped by category
    categories = HelpCategory.objects.filter(
        is_active=True,
        faqs__is_active=True
    ).filter(audience_filter).prefetch_related('faqs').distinct()
    
    # Get uncategorized FAQs
    uncategorized_faqs = FAQ.objects.filter(
        category__isnull=True,
        is_active=True
    ).filter(audience_filter)
    
    context = {
        'categories': categories,
        'uncategorized_faqs': uncategorized_faqs,
        'is_staff': is_staff,
    }
    
    return render(request, 'help/faq_list.html', context)
