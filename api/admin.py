from django.contrib import admin
from .models import PartnerAPIKey


@admin.register(PartnerAPIKey)
class PartnerAPIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active"]
    readonly_fields = ["created_at", "last_used_at"]
    search_fields = ["name"]
    fieldsets = [
        (None, {"fields": ["name", "key", "is_active"]}),
        ("Restrictions", {"fields": ["allowed_origins"], "classes": ["collapse"]}),
        ("Timestamps", {"fields": ["created_at", "last_used_at"]}),
    ]

    def get_readonly_fields(self, request, obj=None):
        # Allow setting a key only when creating a new record.
        if obj:
            return ["key", "created_at", "last_used_at"]
        return ["created_at", "last_used_at"]
