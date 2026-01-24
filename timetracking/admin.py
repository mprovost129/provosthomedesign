from django.contrib import admin
from .models import TimeEntry, ActiveTimer


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    """Admin interface for time entries."""
    
    list_display = ('project', 'user', 'start_time', 'end_time', 'get_duration_display', 
                   'is_billable', 'invoiced', 'created_via_timer')
    list_filter = ('is_billable', 'invoiced', 'created_via_timer', 'user', 'project', 'start_time')
    search_fields = ('description', 'project__job_name', 'user__username', 
                    'user__first_name', 'user__last_name')
    readonly_fields = ('duration', 'created_at', 'updated_at', 'get_duration_decimal')
    date_hierarchy = 'start_time'
    
    fieldsets = (
        ('Time Entry', {
            'fields': ('project', 'user', 'start_time', 'end_time', 'duration', 'get_duration_decimal')
        }),
        ('Details', {
            'fields': ('description', 'is_billable', 'created_via_timer')
        }),
        ('Invoice Information', {
            'fields': ('invoiced', 'invoice', 'hourly_rate'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_duration_decimal(self, obj):
        """Display duration as decimal hours."""
        return f"{obj.get_duration_decimal()} hours"
    get_duration_decimal.short_description = 'Duration (Decimal)'


@admin.register(ActiveTimer)
class ActiveTimerAdmin(admin.ModelAdmin):
    """Admin interface for active timers."""
    
    list_display = ('user', 'get_project', 'started_at', 'get_elapsed')
    readonly_fields = ('started_at', 'get_elapsed')
    
    def get_project(self, obj):
        return obj.time_entry.project.job_name
    get_project.short_description = 'Project'
    
    def get_elapsed(self, obj):
        elapsed = obj.get_elapsed_time()
        total_seconds = int(elapsed.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    get_elapsed.short_description = 'Elapsed Time'

