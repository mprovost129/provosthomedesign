from django import forms
from django.forms import formset_factory
from .checklist_models import ProjectChecklist
from .checklist_services import checked_titles
from .pm_models import ProjectTask


class ChecklistForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    name = forms.CharField(max_length=150, label='Checklist name')
    steps = forms.CharField(max_length=6500, widget=forms.Textarea(attrs={'rows':10}),
        help_text='One step per line. Up to 30 steps, 200 characters each. Due dates and notes are added when applying the checklist.')
    archived = forms.BooleanField(required=False, label='Archive this checklist', help_text='Archived checklists remain editable but cannot be applied to projects.')

    def clean_steps(self):
        return checked_titles([line.strip() for line in self.cleaned_data['steps'].splitlines() if line.strip()])


class ChecklistChoiceForm(forms.Form):
    template = forms.ModelChoiceField(queryset=ProjectChecklist.objects.none(), label='Checklist')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['template'].queryset = ProjectChecklist.objects.filter(archived=False)


class ChecklistStepForm(forms.Form):
    include = forms.BooleanField(required=False, initial=True, label='Add this step')
    title = forms.CharField(max_length=200)
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type':'date'}), label='Due date (optional)')
    notes = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':2}), label='Private notes (optional)')


StepFormSet = formset_factory(ChecklistStepForm, extra=0, max_num=30, validate_max=True, absolute_max=30)


class ChecklistApplyForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    template_id = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class TaskEditForm(forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    class Meta:
        model = ProjectTask
        fields = ['title','due_date','notes']
        widgets = {'due_date':forms.DateInput(attrs={'type':'date'}), 'notes':forms.Textarea(attrs={'rows':4})}
        labels = {'notes':'Private notes','due_date':'Due date (optional)'}
