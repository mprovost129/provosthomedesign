from django.contrib import admin
from .models import HelpCategory, HelpArticle, FAQ


@admin.register(HelpCategory)
class HelpCategoryAdmin(admin.ModelAdmin):
    """Admin interface for help categories."""
    
    list_display = ('name', 'audience', 'order', 'article_count', 'is_active')
    list_filter = ('audience', 'is_active')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    
    fieldsets = (
        ('Category Information', {
            'fields': ('name', 'slug', 'description', 'icon')
        }),
        ('Settings', {
            'fields': ('audience', 'order', 'is_active')
        }),
    )
    
    def article_count(self, obj):
        return obj.articles.filter(is_active=True).count()
    article_count.short_description = 'Active Articles'


@admin.register(HelpArticle)
class HelpArticleAdmin(admin.ModelAdmin):
    """Admin interface for help articles."""
    
    list_display = ('title', 'category', 'audience', 'is_featured', 'views', 'is_active', 'updated_at')
    list_filter = ('category', 'audience', 'is_featured', 'is_active', 'created_at')
    search_fields = ('title', 'summary', 'content')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('views', 'created_at', 'updated_at', 'created_by')
    
    fieldsets = (
        ('Article Information', {
            'fields': ('category', 'title', 'slug', 'summary')
        }),
        ('Content', {
            'fields': ('content',),
            'description': 'You can use HTML for formatting. Common tags: <h3>, <p>, <ul>, <li>, <strong>, <em>, <code>, <pre>'
        }),
        ('Settings', {
            'fields': ('audience', 'order', 'is_featured', 'is_active')
        }),
        ('Metadata', {
            'fields': ('views', 'created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Creating new article
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    """Admin interface for FAQs."""
    
    list_display = ('question', 'category', 'audience', 'order', 'is_active')
    list_filter = ('category', 'audience', 'is_active')
    search_fields = ('question', 'answer')
    
    fieldsets = (
        ('FAQ', {
            'fields': ('question', 'answer', 'category')
        }),
        ('Settings', {
            'fields': ('audience', 'order', 'is_active')
        }),
    )
