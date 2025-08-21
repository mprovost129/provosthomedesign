from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from .models import HouseStyle, Plans, PlanGallery


@admin.register(HouseStyle)
class HouseStyleAdmin(admin.ModelAdmin):
    list_display = ("style_name", "order", "slug")
    prepopulated_fields = {"slug": ("style_name",)}
    search_fields = ("style_name",)
    list_editable = ("order",)
    list_per_page = 50


class PlanGalleryInline(admin.TabularInline):
    model = PlanGallery
    extra = 0
    fields = ("image", "kind", "caption", "order", "uploaded_at", "preview")
    readonly_fields = ("uploaded_at", "preview")
    ordering = ("order", "id")

    @admin.display(description="Preview")
    def preview(self, obj):
        if getattr(obj, "image", None) and getattr(obj.image, "url", None):
            return format_html('<img src="{}" style="max-height:80px;border-radius:6px;" />', obj.image.url)
        return "—"


@admin.register(Plans)
class PlansAdmin(admin.ModelAdmin):
    list_display = (
        "plan_number",
        "house_style",
        "square_footage",
        "bedrooms",
        "bathrooms",
        "stories",
        "garage_stalls",
        "price",
        "is_available",
        "is_featured",
    )
    list_display_links = ("plan_number",)  # keep booleans editable
    list_editable = ("is_available", "is_featured")
    list_filter = ("house_style", "is_available", "is_featured", "bedrooms", "stories", "garage_stalls")
    search_fields = ("plan_number", "description", "house_style__style_name")
    prepopulated_fields = {"slug": ("plan_number",)}
    ordering = ("-is_featured", "-modified_date", "-created_date")
    list_select_related = ("house_style",)
    list_per_page = 50
    inlines = [PlanGalleryInline]
    readonly_fields = ("created_date", "modified_date")

    actions = ("make_featured", "remove_featured", "make_available", "make_unavailable")

    @admin.display(description="Price")
    def price(self, obj):
        return f"${obj.plan_price:,.2f}" if obj.plan_price else "—"

    @admin.action(description="Mark selected plans as Featured")
    def make_featured(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"{updated} plan(s) marked as featured.")

    @admin.action(description="Remove Featured from selected plans")
    def remove_featured(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} plan(s) unfeatured.")

    @admin.action(description="Mark selected plans as Available")
    def make_available(self, request, queryset):
        updated = queryset.update(is_available=True)
        self.message_user(request, f"{updated} plan(s) marked available.")

    @admin.action(description="Mark selected plans as Unavailable")
    def make_unavailable(self, request, queryset):
        updated = queryset.update(is_available=False)
        self.message_user(request, f"{updated} plan(s) marked unavailable.")
