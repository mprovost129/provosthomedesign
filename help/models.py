from django.db import models
from django.contrib.auth.models import User


class HelpCategory(models.Model):
    """Categories for organizing help articles."""
    
    AUDIENCE_CHOICES = [
        ('client', 'Client'),
        ('staff', 'Staff'),
        ('both', 'Both'),
    ]
    
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Font Awesome icon class (e.g., 'fa-file-invoice')")
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, default='both')
    order = models.IntegerField(default=0, help_text="Display order (lower numbers first)")
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name_plural = "Help Categories"
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name


class HelpArticle(models.Model):
    """Individual help articles with step-by-step guides."""
    
    AUDIENCE_CHOICES = [
        ('client', 'Client'),
        ('staff', 'Staff'),
        ('both', 'Both'),
    ]
    
    category = models.ForeignKey(HelpCategory, on_delete=models.CASCADE, related_name='articles')
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    summary = models.CharField(max_length=300, help_text="Brief description shown in listings")
    content = models.TextField(help_text="Full article content (supports HTML)")
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, default='both')
    order = models.IntegerField(default=0, help_text="Display order within category")
    
    # Meta
    is_featured = models.BooleanField(default=False, help_text="Show on main help page")
    is_active = models.BooleanField(default=True)
    views = models.IntegerField(default=0, editable=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta:
        ordering = ['category', 'order', 'title']
    
    def __str__(self):
        return self.title
    
    def increment_views(self):
        """Increment the view counter."""
        self.views += 1
        self.save(update_fields=['views'])


class FAQ(models.Model):
    """Frequently Asked Questions."""
    
    AUDIENCE_CHOICES = [
        ('client', 'Client'),
        ('staff', 'Staff'),
        ('both', 'Both'),
    ]
    
    question = models.CharField(max_length=300)
    answer = models.TextField()
    category = models.ForeignKey(HelpCategory, on_delete=models.CASCADE, related_name='faqs', null=True, blank=True)
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, default='both')
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"
        ordering = ['order', 'question']
    
    def __str__(self):
        return self.question
