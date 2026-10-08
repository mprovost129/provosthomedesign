"""Session-bound recovery, separate from accepted requests and their receipts."""
from datetime import timedelta

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .access import SESSION_SALT, authorized_projects, session_digest
from .client_forms import ClientIntakeForm
from .models import IntakeDraft, PendingUpload, Submission

EXCLUDED = {"intake_token", "upload_ids", "website", "terms_accepted"}


def saved_drafts(request):
    return IntakeDraft.objects.filter(session_digest=session_digest(request), expires_at__gt=timezone.now()).order_by("-updated_at")


def restore_draft(request, draft_id, email):
    draft = saved_drafts(request).filter(pk=draft_id).first()
    if draft is None:
        return None, {}, []
    answers = dict(draft.answers)
    if answers.get("project") and not authorized_projects(email).filter(pk=answers["project"]).exists():
        answers.pop("project", None)
    uploads = list(PendingUpload.objects.filter(pk__in=[file["id"] for file in draft.files],
        intake_nonce=draft.pk, session_digest=draft.session_digest, state="ready", expires_at__gt=timezone.now()))
    ready_ids = {str(upload.pk) for upload in uploads}
    expired = [file["name"] for file in draft.files if file["id"] not in ready_ids]
    answers.update(intake_token=signing.dumps({"nonce": str(draft.pk), "session": draft.session_digest}, salt=SESSION_SALT),
                   upload_ids=",".join(ready_ids), terms_accepted=False)
    return draft, answers, expired


def partial_answers(payload):
    answers = {}
    for name, field in ClientIntakeForm.base_fields.items():
        if name in EXCLUDED or name not in payload:
            continue
        value = payload[name]
        if isinstance(field, forms.MultipleChoiceField):
            if not isinstance(value, list) or len(value) > 10 or any(not isinstance(x, str) for x in value):
                raise ValidationError("The saved form could not be read.")
            value = [x for x in value if x in dict(field.choices)]
        elif isinstance(field, forms.BooleanField):
            value = value is True
        else:
            if not isinstance(value, str) or len(value) > (getattr(field, "max_length", None) or 200):
                raise ValidationError("A saved answer is too long.")
            if name == "project" and value:
                import uuid
                try:
                    value = str(uuid.UUID(value))
                except ValueError:
                    raise ValidationError("The selected project is invalid.") from None
        answers[name] = value
    return answers


@transaction.atomic
def save_draft(*, request, nonce, payload, revision):
    from .services import lock_queue
    lock_queue()
    if Submission.objects.filter(idempotency_key=f"web:{nonce}").exists():
        raise ValidationError("This request has already been submitted. Start a new form for additional work.")
    binding = session_digest(request)
    draft = IntakeDraft.objects.select_for_update().filter(pk=nonce).first()
    if draft and draft.session_digest != binding:
        raise ValidationError("This saved form belongs to a different browser.")
    if (draft.revision if draft else 0) != revision:
        raise ValidationError("This form changed in another tab. Resume the latest saved form before editing further.")
    answers = partial_answers(payload)
    if not draft and saved_drafts(request).count() >= 5:
        raise ValidationError("There are already five saved forms in this browser. Resume or discard an earlier form.")
    files = payload.get("upload_ids", "")
    if not isinstance(files, str) or len(files) > 400:
        raise ValidationError("The uploaded file list is invalid.")
    import uuid
    try:
        ids = [uuid.UUID(x) for x in files.split(",") if x]
    except ValueError:
        raise ValidationError("The uploaded file list is invalid.") from None
    if len(ids) > 10:
        raise ValidationError("Select up to ten files.")
    uploads = PendingUpload.objects.filter(pk__in=ids, intake_nonce=nonce, session_digest=binding,
                                          state="ready", expires_at__gt=timezone.now())
    manifest = [{"id": str(x.pk), "name": x.original_name} for x in uploads]
    if draft and not payload.get("expired_uploads_reviewed"):
        # Keep the warning across further recoveries until the client reviews it.
        old_ids = [file["id"] for file in draft.files]
        expired_ids = {str(x) for x in PendingUpload.objects.filter(pk__in=old_ids,
            intake_nonce=nonce, session_digest=binding).exclude(state="attached").filter(
            expires_at__lte=timezone.now()).values_list("pk", flat=True)}
        manifest += [file for file in draft.files if file["id"] in expired_ids]
    now = timezone.now()
    if draft is None:
        draft = IntakeDraft(id=nonce, session_digest=binding)
    draft.answers, draft.files = answers, manifest
    draft.revision = revision + 1
    draft.updated_at, draft.expires_at = now, now + timedelta(days=7)
    draft.save()
    return draft
