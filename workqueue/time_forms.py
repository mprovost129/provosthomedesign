import uuid
from decimal import Decimal
from django import forms
from django.db.models import Q
from django.utils import timezone
from .models import WorkProject, WorkItem
from .time_models import TimeEntry


class TimerForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    project = forms.ModelChoiceField(queryset=WorkProject.objects.none(), label='Project')
    work_item = forms.ModelChoiceField(queryset=WorkItem.all_objects.none(), label='Job (optional)', required=False)
    category = forms.ChoiceField(choices=TimeEntry.Category.choices, label='Work type')
    description = forms.CharField(max_length=2000, required=False, label='What are you working on? (optional)')
    billable = forms.BooleanField(required=False, label='Billable time')

    def __init__(self, *args, project=None, **kwargs):
        initial = {'token':uuid.uuid4(), 'category':'design', 'billable':True}
        initial.update(kwargs.pop('initial', {}))
        if project:
            initial['project'] = project.pk
        kwargs.setdefault('auto_id', 'timer_%s')
        super().__init__(*args, initial=initial, **kwargs)
        self.fields['project'].queryset = WorkProject.objects.filter(work_items__deleted_at__isnull=True,
            work_items__id__isnull=False).distinct().order_by('name')
        self.fields['work_item'].queryset = WorkItem.objects.select_related('project').order_by('-received_at')
        if project:
            self.fields['project'].queryset = WorkProject.objects.filter(pk=project.pk)
            self.fields['project'].widget = forms.HiddenInput()
            self.fields['work_item'].queryset = self.fields['work_item'].queryset.filter(project=project)

    def clean(self):
        data = super().clean()
        item, project = data.get('work_item'), data.get('project')
        if item and project and item.project_id != project.pk:
            self.add_error('work_item', 'Choose a job belonging to this project.')
        return data


class TimeForm(TimerForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    work_date = forms.DateField(label='Date worked', widget=forms.DateInput(attrs={'type':'date'}))
    minutes = forms.DecimalField(min_value=Decimal('0.01'), max_value=Decimal('1440'), decimal_places=2,
        max_digits=6, label='Minutes worked', help_text='Enter actual time, up to 24 hours per entry. For example: 90 minutes = 1.5 hours.')
    description = forms.CharField(max_length=2000, required=False, label='Work description (optional)', widget=forms.Textarea(attrs={'rows':3}))

    def __init__(self, *args, project, entry=None, **kwargs):
        initial = {'version':0, 'work_date':timezone.localdate()}
        if entry:
            initial.update(token=entry.pk, version=entry.version, work_item=entry.work_item_id,
                work_date=entry.work_date, category=entry.category, description=entry.description, billable=entry.billable,
                minutes=(Decimal(entry.duration_seconds)/60).quantize(Decimal('.01')))
        kwargs['initial'] = initial
        kwargs.setdefault('auto_id', 'id_%s')
        super().__init__(*args, project=project, **kwargs)
        # A historic entry keeps its original job even if that job was archived.
        self.fields['work_item'].queryset = WorkItem.all_objects.filter(project=project).filter(
            Q(deleted_at__isnull=True) | Q(pk=entry.work_item_id if entry else None)).order_by('-received_at')

    def clean_work_date(self):
        value = self.cleaned_data['work_date']
        if value > timezone.localdate():
            raise forms.ValidationError('Time must be recorded for today or an earlier date.')
        return value
