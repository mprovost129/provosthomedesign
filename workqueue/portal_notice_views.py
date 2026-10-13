import uuid
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .models import WorkProject
from .pm_views import editable, error_text, staff_view
from .portal_models import PortalDocumentVersion, PortalMember, ProjectPortal
from .portal_notice_models import PortalInvitation
from .portal_notices import can_invite, configure_reminders, invitation_body, invite, reviewable
from .portal_views import private_view
from .services import EditConflict

SALT = 'workqueue.portal.invitation.preview.v1'


class MemberChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, member):
        return f'{member.contact.full_name or "Contact"} · {member.client.name} · {member.email}'


class InvitationForm(forms.Form):
    recipients = MemberChoices(queryset=PortalMember.objects.none(), widget=forms.CheckboxSelectMultiple,
        label='Approved recipients')
    message = forms.CharField(required=False, max_length=2000, label='Optional message to the client',
        widget=forms.Textarea(attrs={'rows':4}))
    reminders = forms.BooleanField(required=False, label='Remind these contacts after 3 and 7 days if no plan review is submitted')

    def __init__(self, *args, project, revision, **kwargs):
        super().__init__(*args, **kwargs)
        members = list(PortalMember.objects.filter(project=project).select_related('client', 'contact'))
        self.fields['recipients'].queryset = PortalMember.objects.filter(pk__in=[member.pk for member in members if can_invite(member, revision)]).select_related('client', 'contact')
        if not revision or revision.external_url or not revision.storage_key or revision.document.kind != 'plan':
            self.fields['reminders'].disabled = True
            self.fields['reminders'].help_text = 'Reminders are available for uploaded PDF plans with review access.'

    def clean(self):
        data = super().clean()
        if len(data.get('recipients', [])) > 50:
            raise ValidationError('Select at most 50 approved contacts.')
        return data


@staff_view
@private_view
@require_http_methods(['GET', 'POST'])
def invitations(request, project_id):
    editable(request)
    project = get_object_or_404(WorkProject, pk=project_id)
    revision = None
    selected = request.GET.get('document') or request.POST.get('document', '')
    if selected:
        try:
            selected = uuid.UUID(selected)
        except ValueError:
            raise Http404 from None
        revision = get_object_or_404(PortalDocumentVersion.objects.select_related('document'),
            pk=selected, document__project=project, document__withdrawn_at__isnull=True)
    initial = {}
    if request.GET.get('member'):
        initial['recipients'] = [request.GET['member']]
    form = InvitationForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'preview' else None,
        project=project, revision=revision, initial=initial)
    preview, error, status = None, '', 200
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'preview':
                if not form.is_valid():
                    raise ValidationError('Choose recipients and review the message fields.')
                if form.cleaned_data['reminders'] and any(not reviewable(member, revision) for member in form.cleaned_data['recipients']):
                    raise ValidationError('Every selected contact needs plan review permission before enabling review reminders.')
                portal = ProjectPortal.objects.filter(project=project).first()
                snapshot = {'project':str(project.pk), 'actor':request.user.pk, 'document':str(revision.pk) if revision else '',
                    'version':portal.version if portal else 1, 'token':str(uuid.uuid4()),
                    'members':list(form.cleaned_data['recipients'].values_list('pk', flat=True)),
                    'message':form.cleaned_data['message'], 'reminders':form.cleaned_data['reminders']}
                preview = {'signed':signing.dumps(snapshot, salt=SALT, compress=True),
                    'recipients':form.cleaned_data['recipients'], 'body':invitation_body(project, revision, snapshot['message']),
                    'reminders':snapshot['reminders']}
            elif action == 'send':
                snapshot = signing.loads(request.POST.get('preview',''), salt=SALT, max_age=1800)
                if (snapshot['project'] != str(project.pk) or snapshot['actor'] != request.user.pk
                        or snapshot['document'] != (str(revision.pk) if revision else '')):
                    raise ValidationError('This preview belongs to a different project or user. Review it again.')
                if request.POST.get('confirm') != 'on':
                    raise ValidationError('Confirm the recipients and email before sending.')
                invite(project, version=snapshot['version'], token=snapshot['token'], member_ids=snapshot['members'],
                    revision=revision, message=snapshot['message'], reminders=snapshot['reminders'], actor=request.user)
                messages.success(request, 'Invitation emails queued for the selected approved contacts.')
                return redirect('workqueue:portal_invitations', project.pk)
            elif action == 'reminders':
                if request.POST.get('enabled') not in {'0', '1'}:
                    raise ValidationError('Choose whether reminders should be on or off.')
                configure_reminders(int(request.POST.get('invitation', '0')), project=project,
                    version=int(request.POST.get('version', '0')), enabled=request.POST['enabled'] == '1', actor=request.user)
                messages.success(request, 'Review reminder settings saved.')
                return redirect('workqueue:portal_invitations', project.pk)
            else:
                raise ValidationError('Choose an invitation action.')
        except (signing.BadSignature, ValueError, KeyError, PortalInvitation.DoesNotExist):
            error, status = 'This preview expired or changed. Review the email again before sending.', 400
        except (ValidationError, EditConflict) as exc:
            error, status = error_text(exc), 409 if isinstance(exc, EditConflict) else 400
    notices = Paginator(PortalInvitation.objects.filter(batch__project=project).select_related('delivery', 'batch__revision__document').prefetch_related('reminders__delivery').order_by('-batch__created_at', '-pk'), 25).get_page(request.GET.get('page'))
    return render(request, 'workqueue/portal_invitations.html', {'project':project, 'revision':revision, 'form':form,
        'preview':preview, 'error':error, 'notices':notices}, status=status)
