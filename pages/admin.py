from __future__ import annotations

from django import forms
from django.contrib import admin
from django.shortcuts import redirect
from django.utils.html import format_html
from django.db import models
from django.forms import Textarea
from django.db.models import Case, When, IntegerField

from .models import (
    AboutPage,
    ContactMessage,
    ProjectInquiry,
    InquiryAttachment,
    Testimonial,
    SiteSettings,   # single source of truth for brand/contact info
    BusinessHour,   # <-- structured hours
)

# ----------------------------
# Inlines
# ----------------------------

class InquiryAttachmentInline(admin.TabularInline):
    model = InquiryAttachment
    extra = 0
    fields = ("file", "uploaded_at")
    readonly_fields = ("uploaded_at",)
    show_change_link = True


class BusinessHourInline(admin.TabularInline):
    """
    Structured business hours inline under SiteSettings.
    Ensures stable Sunday→Saturday ordering.
    """
    model = BusinessHour
    extra = 0
    can_delete = False
    fields = ("day", "is_closed", "by_appointment", "open_time", "close_time", "note")
    readonly_fields: tuple[str, ...] = ()  # type: ignore

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        order = {code: i for i, (code, _label) in enumerate(BusinessHour.DAYS)}
        return qs.annotate(
            _pos=Case(
                *[When(day=k, then=v) for k, v in order.items()],
                default=99,
                output_field=IntegerField(),
            )
        ).order_by("_pos")

# ----------------------------
# Singleton admin helper
# ----------------------------

class SingletonModelAdmin(admin.ModelAdmin):
    """Allow only a single row; always redirect the changelist to that row."""
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def changelist_view(self, request, extra_context=None):  # type: ignore
        obj = self.model.load()  # model must implement .load()
        return redirect(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
            obj.pk,
        )

# ----------------------------
# Project Inquiries
# ----------------------------

@admin.register(ProjectInquiry)
class ProjectInquiryAdmin(admin.ModelAdmin):
    list_display = (
        "submitted_at",
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "status",
        "display_house_style",
    )
    list_filter = (
        "status",
        "preferred_contact_method",
        "submitted_at",
        "land_purchased",
        "house_style",
    )
    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "city",
        "state",
        "zip_code",
        "additional_notes",
    )
    date_hierarchy = "submitted_at"
    readonly_fields = ("submitted_at",)
    ordering = ("-submitted_at",)
    inlines = [InquiryAttachmentInline]
    list_select_related = ("house_style",)

    @admin.display(description="House style")
    def display_house_style(self, obj):
        hs = getattr(obj, "house_style", None)
        return getattr(hs, "style_name", None) or getattr(hs, "name", None) or str(hs or "")

# ----------------------------
# About (single row, edited in admin)
# ----------------------------

class AboutPageAdminForm(forms.ModelForm):
    """
    - Taller, monospaced textareas.
    - Normalize line endings to '\n' so the site renders consistently.
    """
    class Meta:
        model = AboutPage
        fields = "__all__"
        widgets = {
            # IMPORTANT: this is 'body' (the model field), not 'body_text'
            "body": forms.Textarea(attrs={
                "rows": 14,
                "style": "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
                "placeholder": "One paragraph per line.",
            }),
            "highlights": forms.Textarea(attrs={"rows": 6, "placeholder": "One item per line"}),
            "badges": forms.Textarea(attrs={"rows": 4, "placeholder": "One item per line"}),
            "knowledge_skills": forms.Textarea(attrs={"rows": 8, "placeholder": "One item per line"}),
            "licenses": forms.Textarea(attrs={"rows": 6, "placeholder": "One item per line"}),
        }

    def _normalize_lines(self, text: str) -> str:
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln.strip() for ln in text.split("\n")]
        return "\n".join([ln for ln in lines if ln])

    def clean_body(self):
        return self._normalize_lines(self.cleaned_data.get("body", ""))

    def clean_highlights(self):
        return self._normalize_lines(self.cleaned_data.get("highlights", ""))

    def clean_badges(self):
        return self._normalize_lines(self.cleaned_data.get("badges", ""))

    def clean_knowledge_skills(self):
        return self._normalize_lines(self.cleaned_data.get("knowledge_skills", ""))

    def clean_licenses(self):
        return self._normalize_lines(self.cleaned_data.get("licenses", ""))

@admin.register(AboutPage)
class AboutPageAdmin(admin.ModelAdmin):
    form = AboutPageAdminForm
    # Fallback styling for any other TextField
    formfield_overrides = {
        models.TextField: {
            "widget": Textarea(attrs={
                "rows": 8,
                "style": "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
            })
        },
    }

    def get_fieldsets(self, request, obj=None):
        basics = ["is_published", "title", "owner_name", "subtitle", "body"]
        lists = ("highlights", "badges", "knowledge_skills", "licenses")
        images = ("photo_main", "photo_secondary")

        fieldsets = [
            (None, {"fields": basics}),
            ("Lists (1 item per line)", {"fields": lists}),
            ("Images", {"fields": images}),
        ]
        return fieldsets

# ----------------------------
# Site Settings (single row)
# ----------------------------

@admin.register(SiteSettings)
class SiteSettingsAdmin(SingletonModelAdmin):
    list_display = ("company_name", "contact_email", "contact_phone", "updated")
    readonly_fields = ("logo_preview",)
    inlines = [BusinessHourInline]

    fieldsets = (
        ("Brand", {"fields": ("company_name", "brand_logo", "logo_preview")}),
        ("Primary Contact", {"fields": ("contact_name", "contact_email", "contact_phone")}),
        ("Address", {"fields": ("contact_address",)}),
        # Keep legacy text hours for now (can remove later)
        ("Business Hours (legacy text)", {"fields": ("business_hours",)}),
    )

    def logo_preview(self, obj):
        try:
            if obj.brand_logo:
                return format_html(
                    '<img src="{}" style="max-height:60px;border:1px solid #ddd;padding:2px;" />',
                    obj.brand_logo.url,
                )
        except Exception:
            pass
        return "—"
    logo_preview.short_description = "Logo preview"  # type: ignore

# ----------------------------
# Contact messages
# ----------------------------

if ContactMessage:
    @admin.register(ContactMessage)
    class ContactMessageAdmin(admin.ModelAdmin):
        list_display = ("created_at", "name", "email", "subject", "status")
        list_filter = ("status", "created_at")
        search_fields = ("name", "email", "subject", "message")
        readonly_fields = ("created_at", "ip_address", "user_agent", "referer")
        date_hierarchy = "created_at"
        ordering = ("-created_at",)
        actions = ("mark_read", "mark_replied")

        @admin.action(description="Mark selected as Read")
        def mark_read(self, request, queryset):
            queryset.update(status="read")

        @admin.action(description="Mark selected as Replied")
        def mark_replied(self, request, queryset):
            queryset.update(status="replied")

# ----------------------------
# Testimonials (moderated)
# ----------------------------

@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    """
    Includes the new 'role' field so you can see/filter whether the author is
    an engineer, architect, builder, homeowner, etc.
    """
    list_display = ("created_at", "name", "role", "rating", "approved", "consent_to_publish")
    list_filter = ("approved", "consent_to_publish", "rating", "role", "created_at")
    search_fields = ("name", "email", "message")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    actions = ("approve", "unapprove")

    fieldsets = (
        (None, {
            "fields": ("name", "email", "role", "rating", "message"),
        }),
        ("Publication", {
            "fields": ("consent_to_publish", "approved", "created_at"),
        }),
    )

    @admin.action(description="Approve")
    def approve(self, request, queryset):
        queryset.update(approved=True)

    @admin.action(description="Unapprove")
    def unapprove(self, request, queryset):
        queryset.update(approved=False)
