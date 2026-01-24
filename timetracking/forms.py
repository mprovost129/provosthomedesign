from django import forms
from .models import TimeEntry
from billing.models import Project


class TimeEntryForm(forms.ModelForm):
    """Form for manually creating/editing time entries."""
    
    class Meta:
        model = TimeEntry
        fields = ['project', 'start_time', 'end_time', 'description', 'is_billable']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-select'}),
            'start_time': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'end_time': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'What did you work on?'
            }),
            'is_billable': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        
        if start_time and end_time and end_time <= start_time:
            raise forms.ValidationError('End time must be after start time.')
        
        return cleaned_data


class QuickTimerForm(forms.Form):
    """Simple form for starting a timer from dashboard."""
    
    project = forms.ModelChoiceField(
        queryset=Project.objects.filter(status='active').order_by('job_name'),
        empty_label="Select a project...",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'quick-timer-project'
        })
    )
