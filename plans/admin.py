from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from .models import HouseStyle, PlanComparison, PlanFAQ, PlanGallery, Plans, SavedPlan


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
        return "-"


class PlanFAQInline(admin.StackedInline):
    model = PlanFAQ
    extra = 1
    fields = ("question", "answer", "order")


@admin.register(Plans)
class PlansAdmin(admin.ModelAdmin):
    list_display = (
        "plan_number",
        "plan_name",
        "styles_list",
        "square_footage",
        "bedrooms",
        "bathrooms",
        "stories",
        "garage_stalls",
        "price",
        "is_available",
        "is_featured",
        "is_popular",
    )
    list_display_links = ("plan_number",)  # keep booleans editable
    list_editable = ("is_available", "is_featured", "is_popular")
    list_filter = ("house_styles", "is_available", "is_featured", "is_popular", "bedrooms", "stories", "garage_stalls")
    search_fields = ("plan_number", "plan_name", "description", "house_styles__style_name")
    prepopulated_fields = {"slug": ("plan_number",)}
    ordering = ("-is_featured", "-modified_date", "-created_date")
    filter_horizontal = ("house_styles",)
    list_per_page = 50
    inlines = [PlanGalleryInline, PlanFAQInline]
    readonly_fields = ("created_date", "modified_date")
    fieldsets = (
        ("Identity", {"fields": ("plan_number", "plan_name", "slug", "sku", "house_styles")}),
        ("Core specifications", {"fields": (
            ("square_footage", "bedrooms", "bathrooms"),
            ("stories", "garage_stalls"),
            ("house_width_in", "house_depth_in"),
            "plan_price",
        )}),
        ("Buyer-focused content", {"fields": (
            "description", "ideal_for", "key_features", "layout_highlights",
            "foundation_framing", "exterior_character", "package_contents",
            "delivery_details", "common_modifications",
        )}),
        ("Search attributes", {"fields": (
            ("is_adu", "narrow_lot", "first_floor_primary"),
            ("has_home_office", "has_walk_in_pantry", "has_mudroom"),
            ("has_porch_or_deck", "has_bonus_room", "basement_compatible"),
            "multigenerational",
        )}),
        ("Publishing and SEO", {"fields": (
            "main_image", "meta_description", ("is_available", "is_featured", "is_popular"),
            "created_date", "modified_date",
        )}),
    )

    actions = (
        "make_featured", "remove_featured", "make_popular", "remove_popular",
        "make_available", "make_unavailable",
    )

    @admin.display(description="Styles")
    def styles_list(self, obj):
        return ", ".join([style.style_name for style in obj.house_styles.all()]) or "-"

    @admin.display(description="Price")
    def price(self, obj):
        return f"${obj.plan_price:,.2f}" if obj.plan_price else "-"

    @admin.action(description="Mark selected plans as Featured")
    def make_featured(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"{updated} plan(s) marked as featured.")

    @admin.action(description="Remove Featured from selected plans")
    def remove_featured(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} plan(s) unfeatured.")

    @admin.action(description="Mark selected plans as Popular")
    def make_popular(self, request, queryset):
        updated = queryset.update(is_popular=True)
        self.message_user(request, f"{updated} plan(s) marked as popular.")

    @admin.action(description="Remove Popular from selected plans")
    def remove_popular(self, request, queryset):
        updated = queryset.update(is_popular=False)
        self.message_user(request, f"{updated} plan(s) no longer marked as popular.")

    @admin.action(description="Mark selected plans as Available")
    def make_available(self, request, queryset):
        updated = queryset.update(is_available=True)
        self.message_user(request, f"{updated} plan(s) marked available.")

    @admin.action(description="Mark selected plans as Unavailable")
    def make_unavailable(self, request, queryset):
        updated = queryset.update(is_available=False)
        self.message_user(request, f"{updated} plan(s) marked unavailable.")


@admin.register(SavedPlan)
class SavedPlanAdmin(admin.ModelAdmin):
    list_display = ("session_key_short", "plan", "saved_at")
    list_filter = ("saved_at",)
    search_fields = ("session_key", "plan__plan_number")
    readonly_fields = ("session_key", "plan", "saved_at")
    ordering = ("-saved_at",)
    list_per_page = 100
    
    @admin.display(description="Session")
    def session_key_short(self, obj):
        return f"{obj.session_key[:12]}..."
    
    def has_add_permission(self, request):
        return False


@admin.register(PlanComparison)
class PlanComparisonAdmin(admin.ModelAdmin):
    list_display = ("session_key_short", "plan_count", "created_at", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("session_key",)
    readonly_fields = ("session_key", "created_at", "updated_at")
    filter_horizontal = ("plans",)
    ordering = ("-updated_at",)
    list_per_page = 100
    
    @admin.display(description="Session")
    def session_key_short(self, obj):
        return f"{obj.session_key[:12]}..."
    
    @admin.display(description="Plans")
    def plan_count(self, obj):
        return obj.plans.count()
    
    def has_add_permission(self, request):
        return False
