"""Staff CRM operations, serialized with intake; independent of access grants."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .crm_identity import company_key, email_key, link_item, normalized_text, phone_key
from .models import Client, ClientContact, ClientEvent, WorkItem
from .services import EditConflict, lock_queue


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
    contacts = list(client.contacts.all())
    names = {c.normalized_name for c in contacts if c.normalized_name}
    phones = {c.normalized_phone for c in contacts if c.normalized_phone}
    companies = {c.normalized_company for c in contacts if c.normalized_company}
    if client.kind == "company":
        companies.add(client.normalized_name)
    conditions = (Q(contacts__normalized_name__in=names) | Q(contacts__normalized_phone__in=phones)
                  | Q(contacts__normalized_company__in=companies) | Q(normalized_name__in=companies))
    return Client.objects.filter(merged_into__isnull=True).exclude(pk=client.pk).filter(conditions).distinct().prefetch_related("contacts")[:20]


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
def save_contact(*, client_id, version, contact_id, data, actor):
    lock_queue()
    client = checked_client(client_id, version)
    if contact_id:
        contact = ClientContact.objects.select_for_update().filter(pk=contact_id, client=client).first()
        if contact is None:
            raise ValidationError("Choose a contact belonging to this client.")
    else:
        email = email_key(data.get("email"))
        if not email:
            raise ValidationError("Enter a valid email address.")
        if ClientContact.objects.filter(email=email).exists():
            raise ValidationError("That email already has a client record. Use the merge preview to connect the records.")
        contact = ClientContact(client=client, email=email)
    before = {"name": contact.full_name, "phone": contact.phone}
    contact.full_name = data["full_name"]
    contact.phone = data["phone"]
    contact.normalized_name = normalized_text(contact.full_name)
    contact.normalized_phone = phone_key(contact.phone)
    contact.full_clean()
    contact.save()
    changed(client, actor, "Contact updated" if contact_id else "Contact added",
            {"contact": str(contact.pk), "email": contact.email, "before": before,
             "after": {"name": contact.full_name, "phone": contact.phone}})


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
