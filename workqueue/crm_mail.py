"""Authenticated client correspondence and opt-in daily owner reminders."""
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .crm import require_staff, changed
from .crm_identity import email_key
from .models import (CRMSettings, Client, ClientContact, ClientEmail, ClientFollowUp,
                     MorningDigest, NotificationDelivery, WorkItem, WorkProject)
from .notifications import site_url
from .rich_email import clean_email_html
from .services import EditConflict, lock_queue


def addresses(value):
    result = []
    for value in (value or '').replace(';', ',').split(','):
        if not value.strip():
            continue
        email = email_key(value)
        if not email or '\n' in value or '\r' in value:
            raise ValidationError('Enter email addresses separated by commas.')
        if email not in result:
            result.append(email)
    return result


class ComposeForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    contacts = forms.ModelMultipleChoiceField(queryset=ClientContact.objects.none(),
        widget=forms.CheckboxSelectMultiple, label='To — select client contacts')
    cc = forms.CharField(required=False, max_length=2000, label='CC')
    bcc = forms.CharField(required=False, max_length=2000, label='BCC')
    project = forms.ModelChoiceField(queryset=WorkProject.objects.none(), required=False, label='Project subject')
    project_subject = forms.ChoiceField(required=False, label='Project subject')
    subject = forms.CharField(max_length=255, label='Subject')
    body_html = forms.CharField(max_length=50000, widget=forms.Textarea, label='Message')
    include_signature = forms.BooleanField(required=False, initial=True, label='Include saved signature')

    def __init__(self, *args, client, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contacts'].queryset = client.contacts.filter(archived_at__isnull=True).exclude(email__isnull=True).exclude(email='')
        self.fields['contacts'].label_from_instance = lambda obj: f'{obj.full_name or client.name} — {obj.email}'
        self.fields['project'].queryset = WorkProject.objects.filter(work_items__client_contact__client=client,
                                                                    work_items__deleted_at__isnull=True).distinct()
        self.fields['project'].empty_label = 'Custom subject'
        self.fields['project'].label_from_instance = lambda obj: obj.name
        self.fields['project'].widget = forms.HiddenInput()
        self.project_presets = {f'project:{project.pk}': project for project in self.fields['project'].queryset}
        self.work_presets = {f'work:{item.pk}': item for item in WorkItem.objects.filter(
            client_contact__client=client, project__isnull=True).order_by('project_name', 'reference')}
        self.fields['project_subject'].choices = [('', 'Custom subject')]
        self.fields['project_subject'].choices += [(key, project.name) for key, project in self.project_presets.items()]
        self.fields['project_subject'].choices += [(key, item.project_name or item.project_context or item.project_street or item.reference)
            for key, item in self.work_presets.items()]
        self.fields['cc'].help_text = self.fields['bcc'].help_text = 'Separate addresses with commas. CC is visible to recipients; BCC is hidden.'

    def clean_subject(self):
        value = self.cleaned_data['subject']
        if '\r' in value or '\n' in value:
            raise ValidationError('Use a single-line subject.')
        return value

    def clean_cc(self):
        return addresses(self.cleaned_data.get('cc'))

    def clean_bcc(self):
        return addresses(self.cleaned_data.get('bcc'))

    def clean_body_html(self):
        html, text = clean_email_html(self.cleaned_data['body_html'])
        if not text:
            raise ValidationError('Write a message before sending.')
        return html

    def clean(self):
        data = super().clean()
        preset = data.get('project_subject', '')
        if 'project_subject' in self.data:
            data['project'] = self.project_presets.get(preset)
            if preset in self.work_presets:
                data['selected_item'] = self.work_presets[preset]
        to = [c.email for c in data.get('contacts', [])]
        cc, bcc = data.get('cc', []), data.get('bcc', [])
        if len(set(to + cc + bcc)) > 25:
            raise ValidationError('Use no more than 25 recipients per message.')
        if len(set(to + cc + bcc)) != len(to + cc + bcc):
            raise ValidationError('Each recipient should appear only once, in To, CC or BCC.')
        return data


@transaction.atomic
def compose_email(*, client_id, item_id, data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    existing = ClientEmail.objects.filter(pk=data['token']).first()
    if existing:
        if existing.actor_id != actor.pk or existing.client_id != client_id:
            raise ValidationError('This message token belongs to another draft.')
        return existing, False
    client = Client.objects.select_for_update().get(pk=client_id, merged_into__isnull=True)
    ids = [contact.pk for contact in data['contacts']]
    contacts = list(client.contacts.filter(pk__in=ids, archived_at__isnull=True).exclude(email__isnull=True).exclude(email=''))
    if not contacts or len(contacts) != len(ids):
        raise ValidationError('A selected contact changed. Reopen the editor and review recipients.')
    project = data.get('project')
    if project and not WorkItem.objects.filter(project=project, client_contact__client=client).exists():
        raise ValidationError('Choose a project belonging to this client.')
    if data.get('selected_item'):
        item_id = data['selected_item'].pk
    item = WorkItem.objects.filter(pk=item_id, client_contact__client=client).first() if item_id else None
    if item_id and not item:
        raise ValidationError('This work item is no longer available.')
    html = data['body_html']
    preferences = CRMSettings.objects.filter(pk=1).first()
    if data.get('include_signature') and preferences and preferences.signature_html:
        html += '<p><br></p>' + preferences.signature_html
    html, text = clean_email_html(html)
    to = sorted(c.email for c in contacts)
    cc, bcc = data.get('cc', []), data.get('bcc', [])
    if len(set(to + cc + bcc)) != len(to + cc + bcc):
        raise ValidationError('Recipients changed. Review To, CC and BCC before sending.')
    delivery = NotificationDelivery.objects.create(recipient_kind='client', recipient=to[0],
        to_recipients=to, cc_recipients=cc, bcc_recipients=bcc, subject=data['subject'], body=text,
        html_body=html, provider_reference=f'queue-{uuid.uuid4().hex}')
    message = ClientEmail.objects.create(id=data['token'], client=client, project=project, work_item=item,
        actor=actor, delivery=delivery, subject=data['subject'], body_html=html, body_text=text, to=to, cc=cc, bcc=bcc)
    changed(client, actor, 'Client email prepared', {'subject': message.subject, 'message': str(message.pk)})
    return message, True


class PreferencesForm(forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = CRMSettings
        fields = ['morning_reminders', 'signature_html']
        labels = {'morning_reminders': 'Email me a morning summary when something needs attention',
                  'signature_html': 'Email signature'}
        widgets = {'signature_html': forms.Textarea(attrs={'rows': 5})}

    def clean_signature_html(self):
        value = self.cleaned_data['signature_html']
        if len(value) > 10000:
            raise ValidationError('Keep the signature under 10,000 characters.')
        html, text = clean_email_html(value)
        return html if text else ''


@transaction.atomic
def save_preferences(data, actor):
    require_staff(actor, 'workqueue.change_workitem')
    lock_queue()
    prefs, _ = CRMSettings.objects.get_or_create(pk=1)
    if prefs.version != data['version']:
        raise EditConflict('Settings changed. Reload before saving again.')
    prefs.morning_reminders, prefs.signature_html = data['morning_reminders'], data['signature_html']
    prefs.version += 1
    prefs.save()
    if not prefs.morning_reminders:
        NotificationDelivery.objects.filter(morning_digest__isnull=False, state='pending').update(state='canceled', body='')
    return prefs


@transaction.atomic
def queue_morning_digest(now=None):
    """At most one actionable weekday digest per local date; no weekend or late-night catch-up."""
    now = (now or timezone.now()).astimezone(ZoneInfo('America/New_York'))
    if now.weekday() >= 5 or not 8 <= now.hour < 12:
        return None
    lock_queue()
    prefs = CRMSettings.objects.filter(pk=1, morning_reminders=True).first()
    if not prefs or MorningDigest.objects.filter(date=now.date()).exists():
        return None
    tasks = ClientFollowUp.objects.filter(client__merged_into__isnull=True, completed_at__isnull=True,
        canceled_at__isnull=True, due_date__lte=now.date()).select_related('client')
    jobs = WorkItem.objects.active().filter(Q(followup_date__lte=now.date()) | Q(committed_due_date__lt=now.date()))
    lines = []
    for task in tasks:
        lines.append(f'{task.client.name}: {task.title} — follow up {task.due_date:%b %d}\n'
                     f'{site_url(reverse("workqueue:client_detail", args=[task.client_id]))}#follow-ups')
    for job in jobs:
        reasons = []
        if job.followup_date and job.followup_date <= now.date():
            reasons.append(f'follow up {job.followup_date:%b %d}')
        if job.committed_due_date and job.committed_due_date < now.date():
            reasons.append(f'overdue since {job.committed_due_date:%b %d}')
        lines.append(f'{job.reference} · {job.project_name or job.contact_full_name}: {", ".join(reasons)}\n'
                     f'{site_url(reverse("workqueue:queue"))}?scope=all&q={job.reference}#request-{job.pk}')
    if not lines:
        return None
    owner = email_key(getattr(settings, 'INTAKE_OWNER_EMAIL', ''))
    if not owner:
        raise ValidationError('The owner reminder email address is not configured.')
    delivery = NotificationDelivery.objects.create(recipient_kind='owner', recipient=owner,
        subject=f'Provost Home Design: {len(lines)} item(s) need attention',
        body='Good morning,\n\n' + '\n\n'.join(lines) + '\n\nManage morning reminders:\n' + site_url(reverse('workqueue:communication_settings')),
        provider_reference=f'queue-{uuid.uuid4().hex}')
    MorningDigest.objects.create(date=now.date(), delivery=delivery)
    return delivery
