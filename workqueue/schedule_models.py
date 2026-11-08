"""Owner-maintained design capacity; separate from appointment availability."""
from datetime import date, time
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q

class DesignSchedule(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    design_hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=5,
        validators=[MinValueValidator(Decimal("0.25")), MaxValueValidator(24)])
    workday_start = models.TimeField(default=time(8, 30))
    workday_end = models.TimeField(default=time(15, 30))
    window_business_days = models.PositiveSmallIntegerField(default=2, validators=[MaxValueValidator(10)])
    days_off = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name="wq_schedule_singleton"),
            models.CheckConstraint(condition=Q(design_hours_per_day__gte=Decimal("0.25"), design_hours_per_day__lte=24), name="wq_design_capacity_range"),
            models.CheckConstraint(condition=Q(workday_end__gt=models.F("workday_start")), name="wq_design_workday_order"),
            models.CheckConstraint(condition=Q(window_business_days__lte=10), name="wq_design_window_range")]

    def clean(self):
        super().clean()
        if self.workday_start and self.workday_end:
            start = self.workday_start.hour * 60 + self.workday_start.minute
            end = self.workday_end.hour * 60 + self.workday_end.minute
            if end <= start:
                raise ValidationError({"workday_end": "End time must be after start time on the same day."})
            if self.design_hours_per_day is not None and self.design_hours_per_day * 60 > end - start:
                raise ValidationError({"design_hours_per_day": "Design time cannot exceed the workday length."})
        if not isinstance(self.days_off, list) or len(self.days_off) > 500:
            raise ValidationError({"days_off": "Enter at most 500 dates."})
        for value in self.days_off:
            try:
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValidationError({"days_off": "Use YYYY-MM-DD dates."}) from None
