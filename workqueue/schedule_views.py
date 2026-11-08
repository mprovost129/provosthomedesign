from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .forecasting import get_schedule
from .models import DesignSchedule
from .services import EditConflict, lock_queue
from .views import queue_enabled, staff_preview

class DesignScheduleForm(forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    days_off_text = forms.CharField(label="Days off and holidays", required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One YYYY-MM-DD date per line. These dates and weekends are excluded from every delivery window.")
    class Meta:
        model = DesignSchedule
        fields = ["design_hours_per_day", "workday_start", "workday_end", "window_business_days"]
        labels = {"design_hours_per_day": "Actual design hours per day", "workday_start": "Workday starts (Eastern)",
            "workday_end": "Workday ends (Eastern)", "window_business_days": "Window allowance (working days)"}
        widgets = {"workday_start": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
            "workday_end": forms.TimeInput(format="%H:%M", attrs={"type": "time"})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["version"] = self.instance.version
        self.initial["days_off_text"] = "\n".join(self.instance.days_off)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
    def clean_days_off_text(self):
        values = self.cleaned_data["days_off_text"].split()
        try:
            if len(values) > 500:
                raise ValueError
            return sorted({date.fromisoformat(value).isoformat() for value in values})
        except ValueError:
            raise ValidationError("Enter at most 500 dates, one YYYY-MM-DD date per line.") from None

@transaction.atomic
def save_schedule(*, data, version, actor):
    lock_queue()
    schedule, _ = DesignSchedule.objects.select_for_update().get_or_create(pk=1)
    if schedule.version != version:
        raise EditConflict("The design schedule changed in another tab. Reload this page before saving again.")
    for name in DesignScheduleForm.Meta.fields:
        setattr(schedule, name, data[name])
    schedule.days_off = data["days_off_text"]
    schedule.full_clean()
    schedule.version += 1
    schedule.updated_by = actor
    schedule.save()
    return schedule

@queue_enabled
@staff_member_required(login_url="workqueue:staff_login")
@permission_required(("workqueue.view_workitem", "workqueue.change_workitem"), raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def design_schedule(request):
    if staff_preview(request):
        raise PermissionDenied
    form = DesignScheduleForm(request.POST or None, instance=get_schedule())
    status = 200
    if request.method == "POST":
        if form.is_valid():
            try:
                save_schedule(data=form.cleaned_data, version=form.cleaned_data["version"], actor=request.user)
            except (ValidationError, EditConflict) as exc:
                form.add_error(None, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                status = 409 if isinstance(exc, EditConflict) else 400
            else:
                messages.success(request, "Design capacity saved. Delivery estimates now use this schedule. No client emails were sent.")
                return redirect("workqueue:design_schedule")
        else:
            status = 400
    return render(request, "workqueue/design_schedule.html", {"form": form}, status=status)
