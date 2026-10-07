import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .access import normalize_email, resolve_project
from .models import Attachment, PendingUpload, ProjectClaim, Submission, WorkItem
from .notifications import queue_receipts
from .services import create_work


@transaction.atomic
def submit_intake(*, data, nonce, binding, verified_email="", labels=None):
    # Shared allocation/service lock also serializes retries and queue-position snapshots.
    from .services import lock_queue
    lock_queue()
    key = f"web:{nonce}"
    existing = Submission.objects.select_related("work_item").filter(idempotency_key=key).first()
    if existing:
        return existing.work_item, False
    try:
        upload_ids = [uuid.UUID(value) for value in data.get("upload_ids", "").split(",") if value]
    except ValueError:
        raise ValidationError("One of the attachment references is invalid. Please upload that file again.") from None
    if len(upload_ids) != len(set(upload_ids)) or len(upload_ids) > 10:
        raise ValidationError("Select up to 10 different uploaded files.")
    uploads = list(PendingUpload.objects.select_for_update().filter(pk__in=upload_ids, intake_nonce=nonce,
        session_digest=binding, state=PendingUpload.State.READY, expires_at__gt=timezone.now()))
    if len(uploads) != len(upload_ids):
        raise ValidationError("An attachment is incomplete or expired. Upload it again before submitting.")
    project, previous = (None, None)
    if data["kind"] == "update" and normalize_email(data["contact_email"]) == verified_email:
        project, previous = resolve_project(verified_email, reference=data.get("project_reference", ""),
            context=data.get("project_context", ""), project_id=getattr(data.get("project"), "pk", ""))
    fields = {name: data.get(name, "") for name in ["contact_full_name", "company", "contact_email", "contact_phone",
        "billing_street", "billing_city", "billing_state", "billing_zip", "project_name", "project_street",
        "project_city", "project_state", "project_zip", "service_needed"]}
    fields["contact_email"] = normalize_email(fields["contact_email"])
    for name in fields:
        if fields[name] is None:
            fields[name] = ""
    fields.update(kind=data["kind"], project=project, previous_request=previous,
        description=data["new_description"] if data["kind"] == "new" else data["update_description"],
        project_context=data.get("project_reference") or data.get("project_context") or "",
        requested_deadline=data.get("requested_deadline"), source="Website")
    item, created = create_work(data=fields, actor=None, idempotency_key=key)
    submission = Submission.objects.get(idempotency_key=key)
    submission.channel = "website"
    excluded = {"intake_token", "upload_ids", "website", "project", "same_address"}
    answers = {}
    for name, value in data.items():
        if name not in excluded and value not in (None, "", [], False):
            answers[(labels or {}).get(name, name)] = value if isinstance(value, list) else str(value)
    submission.answers = answers
    submission.owner_email = fields["contact_email"]
    submission.save(update_fields=["channel", "answers", "owner_email"])
    for upload in uploads:
        attachment = Attachment.objects.create(submission=submission, original_name=upload.original_name,
            storage_key=upload.storage_key, content_type=upload.content_type, size_bytes=upload.size_bytes,
            categories=data.get("categories", []))
        upload.state = PendingUpload.State.ATTACHED
        upload.attachment = attachment
        upload.save(update_fields=["state", "attachment"])
    if data["kind"] == "new":
        claim = ProjectClaim.objects.create(project=item.project, submission=submission, email=fields["contact_email"])
        if verified_email == fields["contact_email"]:
            from .models import ProjectAccess
            ProjectAccess.objects.get_or_create(project=item.project, email=verified_email)
            claim.verified_at = timezone.now()
            claim.save(update_fields=["verified_at"])
    queue_receipts(submission)
    return item, created
