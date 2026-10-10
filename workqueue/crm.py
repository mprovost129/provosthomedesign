"""Staff CRM operations, serialized with intake; independent of access grants."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .crm_identity import company_key, email_key, link_item, normalized_text, phone_key
from .models import Attachment, Client, ClientContact, ClientEvent, PendingUpload, Submission, WorkItem
from .services import EditConflict, lock_queue


def require_staff(actor, permission):
    from django.core.exceptions import PermissionDenied
    if not (actor and actor.is_active and actor.is_staff and actor.has_perms(
            ("workqueue.view_workitem", permission))):
        raise PermissionDenied


@transaction.atomic
def create_client(*, data, nonce, actor):
    """The session-bound nonce prevents duplicate phone-only entries on retry."""
    require_staff(actor, "workqueue.change_workitem")
    lock_queue()
    existing = Client.objects.filter(pk=nonce).first()
    if existing:
        return existing.merged_into or existing, False
    email = email_key(data.get("email"))
    if data.get("email") and not email:
        raise ValidationError("Enter a valid email address or leave it blank.")
    contact = ClientContact.objects.select_related("client").filter(email=email).first() if email else None
    if contact:
        return contact.client, False
    client = Client(id=nonce, **{name: data[name] for name in (
        "name", "kind", "billing_street", "billing_city", "billing_state", "billing_zip", "internal_notes")})
    client.normalized_name = normalized_text(client.name)
    client.full_clean()
    client.save()
    full_name = data.get("full_name") or (client.name if client.kind == Client.Kind.INDIVIDUAL else "")
    contact = ClientContact(client=client, email=email, full_name=full_name, phone=data.get("phone", ""),
        submitted_company=client.name if client.kind == Client.Kind.COMPANY else "Homeowner",
        normalized_name=normalized_text(full_name), normalized_phone=phone_key(data.get("phone", "")),
        normalized_company=normalized_text(client.name) if client.kind == Client.Kind.COMPANY else "")
    contact.full_clean()
    contact.save()
    ClientEvent.objects.create(client=client, actor=actor, action="Client added manually", changes={"contact": str(contact.pk)})
    return client, True


@transaction.atomic
def create_client_work(*, client_id, version, data, nonce, binding, actor):
    """Explicit staff association, validated again under the shared queue lock."""
    import uuid
    from django.utils import timezone
    from .services import create_work
    require_staff(actor, "workqueue.add_workitem")
    lock_queue()
    key = f"crm:{actor.pk}:{client_id}:{nonce}"
    existing = Submission.objects.select_related("work_item").filter(idempotency_key=key).first()
    if existing:
        return existing.work_item, False
    client = checked_client(client_id, version)
    contact = client.contacts.filter(pk=data["contact"].pk, archived_at__isnull=True).first()
    if contact is None:
        raise ValidationError("Choose a contact belonging to this client.")
    project = data.get("project")
    if project and not WorkItem.objects.filter(project=project, client_contact__client=client).exists():
        raise ValidationError("Choose one of this client's projects.")
    if data["kind"] == "update" and not project:
        raise ValidationError("Select the existing project for this update.")
    try:
        ids = [uuid.UUID(value) for value in data.get("upload_ids", "").split(",") if value]
    except ValueError:
        raise ValidationError("The file list is invalid.") from None
    if len(ids) > 10 or len(ids) != len(set(ids)):
        raise ValidationError("Select up to 10 different uploaded files.")
    uploads = list(PendingUpload.objects.select_for_update().filter(pk__in=ids, intake_nonce=nonce,
        session_digest=binding, state="ready", expires_at__gt=timezone.now()))
    if len(uploads) != len(ids):
        raise ValidationError("A file is incomplete or expired. Upload it again before saving.")
    fields = {name: data.get(name, "") for name in (
        "kind", "project_name", "service_needed", "description", "project_street", "project_city", "project_state", "project_zip")}
    fields.update(project=project, contact_full_name=contact.full_name or client.name,
        company=client.name if client.kind == Client.Kind.COMPANY else "Homeowner",
        contact_email=contact.email or "", contact_phone=contact.phone,
        requested_deadline=data.get("requested_deadline"), source="Manual CRM entry")
    for name in ("billing_street", "billing_city", "billing_state", "billing_zip"):
        fields[name] = getattr(client, name)
    if project:
        fields.update(project_name=project.name, project_street=project.street,
            project_city=project.city, project_state=project.state, project_zip=project.zip_code)
    item, created = create_work(data=fields, actor=actor, idempotency_key=key, staff_contact=contact)
    submission = item.submissions.get(idempotency_key=key)
    # Use readable, frozen labels instead of database field names in history.
    submission.answers = {name.replace("_", " ").capitalize(): value
                          for name, value in submission.answers.items()}
    submission.answers["Kind"] = item.get_kind_display()
    submission.save(update_fields=["answers"])
    for upload in uploads:
        attachment = Attachment.objects.create(submission=submission, original_name=upload.original_name,
            storage_key=upload.storage_key, content_type=upload.content_type, size_bytes=upload.size_bytes)
        upload.state, upload.attachment = "attached", attachment
        upload.save(update_fields=["state", "attachment"])
    return item, created


def attach_client(item):
    # create_work already holds the queue lock and a transaction.
    if link_item(item, Client, ClientContact):
        client = item.client_contact.client
        client.version += 1
        client.save(update_fields=["version", "updated_at"])
        ClientEvent.objects.create(client=client, action="Request added",
                                   changes={"request": item.reference})


@transaction.atomic
def sync_clients():
    if not WorkItem.objects.filter(client_contact__isnull=True).exists():
        return 0
    lock_queue()
    count = 0
    for item in WorkItem.objects.filter(client_contact__isnull=True).order_by("received_at", "id").iterator():
        if link_item(item, Client, ClientContact, fill_blanks=True):
            count += 1
    return count


def possible_matches(client):
    contacts = list(client.contacts.filter(archived_at__isnull=True))
    names = {c.normalized_name for c in contacts if c.normalized_name}
    phones = {c.normalized_phone for c in contacts if c.normalized_phone}
    companies = {c.normalized_company for c in contacts if c.normalized_company}
    if client.kind == "company":
        companies.add(client.normalized_name)
    conditions = (Q(contacts__normalized_name__in=names) | Q(contacts__normalized_phone__in=phones)
                  | Q(contacts__normalized_company__in=companies) | Q(normalized_name__in=companies))
    return Client.objects.filter(merged_into__isnull=True).exclude(pk=client.pk).filter(conditions,
        contacts__archived_at__isnull=True).distinct().prefetch_related("contacts")[:20]


def checked_client(client_id, version):
    client = Client.objects.select_for_update().get(pk=client_id)
    if client.version != version or client.merged_into_id:
        raise EditConflict("This client changed. Reload the page before saving again.")
    return client


def changed(client, actor, action, changes):
    client.version += 1
    client.save()
    ClientEvent.objects.create(client=client, actor=actor, action=action, changes=changes)


@transaction.atomic
def edit_client(*, client_id, version, data, actor):
    lock_queue()
    client = checked_client(client_id, version)
    allowed = {"name", "kind", "billing_street", "billing_city", "billing_state", "billing_zip", "internal_notes"}
    if set(data) - allowed:
        raise ValidationError("Unsupported client field.")
    changes = {}
    for key, value in data.items():
        old = getattr(client, key)
        if old != value:
            changes[key] = {"before": old, "after": value}
            setattr(client, key, value)
    client.normalized_name = normalized_text(client.name)
    client.full_clean()
    if changes:
        changed(client, actor, "Client details updated", changes)
    return client


@transaction.atomic
def save_contact(*, client_id, version, contact_id, data, actor, copy_from_id=None):
    require_staff(actor, "workqueue.change_workitem")
    lock_queue()
    client = checked_client(client_id, version)
    source = None
    if copy_from_id:
        source = client.contacts.filter(pk=copy_from_id, archived_at__isnull=True).first()
        if contact_id or source is None:
            raise ValidationError("Choose an active contact belonging to this client to copy.")
    if contact_id:
        contact = ClientContact.objects.select_for_update().filter(pk=contact_id, client=client, archived_at__isnull=True).first()
        if contact is None:
            raise ValidationError("Choose a contact belonging to this client.")
    else:
        contact = ClientContact(client=client, submitted_company=source.submitted_company if source else "",
            normalized_company=source.normalized_company if source else "")
    # Omitted email preserves older open forms/callers; an explicitly blank
    # email clears it to NULL so multiple unknown contacts remain distinct.
    raw_email = data.get("email") if "email" in data else contact.email
    email = email_key(raw_email)
    if raw_email and not email:
        raise ValidationError("Enter a valid email address.")
    if not contact_id and not (email or data.get("full_name") or data.get("phone")):
        raise ValidationError("Enter a name, email address or phone number for the contact.")
    if email and ClientContact.objects.filter(email=email).exclude(pk=contact.pk).exists():
        raise ValidationError("That email already belongs to another contact. Use the existing contact or the merge preview to connect client records.")
    before = {"name": contact.full_name, "phone": contact.phone, "email": contact.email}
    contact.email = email
    contact.full_name = data["full_name"]
    contact.phone = data["phone"]
    contact.normalized_name = normalized_text(contact.full_name)
    contact.normalized_phone = phone_key(contact.phone)
    contact.full_clean()
    contact.save()
    changed(client, actor, "Contact copied" if copy_from_id else "Contact updated" if contact_id else "Contact added",
            {"contact": str(contact.pk), "email": contact.email, "before": before,
             "after": {"name": contact.full_name, "phone": contact.phone, "email": contact.email},
             **({"copied_from": str(copy_from_id)} if copy_from_id else {})})
    return contact


@transaction.atomic
def archive_contact(*, client_id, version, contact_id, actor):
    from django.utils import timezone
    require_staff(actor, "workqueue.change_workitem")
    lock_queue()
    client = checked_client(client_id, version)
    contact = client.contacts.select_for_update().filter(pk=contact_id, archived_at__isnull=True).first()
    if contact is None:
        raise ValidationError("Choose an active contact belonging to this client.")
    contact.archived_at, contact.archived_email = timezone.now(), contact.email or ""
    # Release the unique email for future intake without changing historical
    # work snapshots, submission ownership, email deliveries or access grants.
    contact.email = None
    contact.save(update_fields=["archived_at", "archived_email", "email"])
    changed(client, actor, "Contact deleted", {"contact": str(contact.pk),
        "name": contact.full_name, "email": contact.archived_email})
    return contact


@transaction.atomic
def restore_contact(*, client_id, version, contact_id, actor, restore_email=True):
    require_staff(actor, "workqueue.change_workitem")
    lock_queue()
    client = checked_client(client_id, version)
    contact = client.contacts.select_for_update().filter(pk=contact_id, archived_at__isnull=False).first()
    if contact is None:
        raise ValidationError("Choose a deleted contact belonging to this client.")
    email = email_key(contact.archived_email) if restore_email else None
    if email and ClientContact.objects.filter(email=email).exclude(pk=contact.pk).exists():
        raise ValidationError("The original email is now used by another contact. Uncheck 'Restore original email' to restore without it, or connect the client records first.")
    contact.email, contact.archived_at = email, None
    contact.full_clean()
    contact.save(update_fields=["email", "archived_at"])
    changed(client, actor, "Contact restored", {"contact": str(contact.pk), "email": contact.email,
        "original_email": contact.archived_email})
    return contact


@transaction.atomic
def merge_clients(*, source_id, source_version, target_id, target_version, actor):
    lock_queue()
    if source_id == target_id:
        raise ValidationError("Choose another client to keep.")
    source = checked_client(source_id, source_version)
    target = checked_client(target_id, target_version)
    contacts = list(source.contacts.values_list("pk", flat=True))
    ClientContact.objects.filter(client=source).update(client=target)
    source.merged_into = target
    changed(source, actor, "Merged into another client", {"target": str(target.pk), "name": target.name})
    changed(target, actor, "Client records merged", {"source": str(source.pk), "name": source.name,
        "contacts": [str(pk) for pk in contacts], "source_version": source.version,
        "target_version": target.version + 1})
    return target


@transaction.atomic
def undo_merge(*, client_id, version, event_id, actor):
    lock_queue()
    target = checked_client(client_id, version)
    event = ClientEvent.objects.filter(client=target, pk=event_id, action="Client records merged").first()
    if event is None:
        raise ValidationError("Choose a recorded merge.")
    source = Client.objects.select_for_update().get(pk=event.changes["source"])
    ids = event.changes["contacts"]
    if (target.version != event.changes["target_version"] or source.version != event.changes["source_version"]
            or source.merged_into_id != target.pk
            or ClientContact.objects.filter(pk__in=ids, client=target).count() != len(ids)):
        raise EditConflict("These records changed after the merge. Review their contact history before correcting them.")
    ClientContact.objects.filter(pk__in=ids, client=target).update(client=source)
    source.merged_into = None
    changed(source, actor, "Merge reversed", {"target": str(target.pk), "merge": event.pk})
    changed(target, actor, "Merge reversed", {"source": str(source.pk), "merge": event.pk})
    return source
