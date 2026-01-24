from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from billing.models import Project
from datetime import timedelta


class TimeEntry(models.Model):
    """
    Individual time entry for tracking work on projects.
    Can be created manually or via timer.
    """
    
    # Relationships
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='time_entries',
        help_text="Project this time was spent on"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='time_entries',
        help_text="User who logged this time"
    )
    
    # Time tracking
    start_time = models.DateTimeField(
        help_text="When work started"
    )
    end_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When work ended (null if timer is running)"
    )
    duration = models.DurationField(
        null=True,
        blank=True,
        help_text="Calculated duration (end_time - start_time)"
    )
    
    # Entry details
    description = models.TextField(
        blank=True,
        help_text="What was worked on during this time"
    )
    is_billable = models.BooleanField(
        default=True,
        help_text="Whether this time should be billed to client"
    )
    
    # Invoice tracking
    invoiced = models.BooleanField(
        default=False,
        help_text="Whether this time entry has been added to an invoice"
    )
    invoice = models.ForeignKey(
        'billing.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='time_entries',
        help_text="Invoice this time entry was added to"
    )
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Hourly rate used when invoiced"
    )
    
    # Tracking metadata
    created_via_timer = models.BooleanField(
        default=False,
        help_text="True if created via timer, False if manually entered"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-start_time']
        verbose_name = 'Time Entry'
        verbose_name_plural = 'Time Entries'
        indexes = [
            models.Index(fields=['project', '-start_time']),
            models.Index(fields=['user', '-start_time']),
            models.Index(fields=['start_time']),
        ]
    
    def __str__(self):
        duration_str = self.get_duration_display()
        return f"{self.project.job_name} - {duration_str} - {self.user.get_full_name()}"
    
    def save(self, *args, **kwargs):
        """Calculate duration when end_time is set."""
        if self.end_time and self.start_time:
            self.duration = self.end_time - self.start_time
        super().save(*args, **kwargs)
    
    def get_duration_display(self):
        """Return human-readable duration."""
        if not self.duration:
            if not self.end_time:
                # Timer is running
                elapsed = timezone.now() - self.start_time
                return self._format_duration(elapsed)
            return "0:00"
        return self._format_duration(self.duration)
    
    def _format_duration(self, duration):
        """Format timedelta as HH:MM."""
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}:{minutes:02d}"
    
    def get_duration_decimal(self):
        """Return duration as decimal hours for billing."""
        if not self.duration:
            if not self.end_time:
                elapsed = timezone.now() - self.start_time
                return round(elapsed.total_seconds() / 3600, 2)
            return 0
        return round(self.duration.total_seconds() / 3600, 2)
    
    def is_running(self):
        """Check if this is an active timer."""
        return self.end_time is None
    
    def stop_timer(self):
        """Stop the timer and calculate duration."""
        if self.is_running():
            self.end_time = timezone.now()
            self.duration = self.end_time - self.start_time
            self.save()
            return True
        return False


class ActiveTimer(models.Model):
    """
    Tracks the currently active timer for each user.
    Only one timer can be active per user at a time.
    """
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='active_timer',
        help_text="User with active timer"
    )
    time_entry = models.OneToOneField(
        TimeEntry,
        on_delete=models.CASCADE,
        related_name='active_timer_ref',
        help_text="The active time entry"
    )
    started_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Active Timer'
        verbose_name_plural = 'Active Timers'
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.time_entry.project.job_name}"
    
    def get_elapsed_time(self):
        """Get current elapsed time."""
        return timezone.now() - self.time_entry.start_time
    
    def stop(self):
        """Stop the timer and delete this active timer record."""
        self.time_entry.stop_timer()
        self.delete()

