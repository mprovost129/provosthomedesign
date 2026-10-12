"""Staff-only CRM summaries derived from existing records, never email inbox access."""
from collections import defaultdict

from django.core.paginator import Paginator
from django.db.models import CharField, Exists, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Cast, Coalesce
from django.urls import reverse
from django.utils import timezone

from .models import (Appointment, AuditEvent, Client, ClientContact, ClientEvent,
                     ClientFollowUp, ClientEmail, CompletedDelivery, NotificationDelivery, Status, Submission, WorkItem)


WORK_ACTIVITY_LABELS = {
    "edited": "Work details updated", "project_details_edited": "Project details updated",
    "milestone_email_decided": "Client status email decision", "queue_reordered": "Queue order changed",
    "legacy_imported": "Work imported from the previous queue", "prior_link_cleared": "Previous request connection removed",
    "copied": "Work copied", "restored": "Work restored", "access_granted": "Project access granted",
    "access_revoked": "Project access removed", "email_retry_requested": "Email retry requested",
    "completed_files_released": "Completed files shared", "information_requested": "More information requested",
    "information_response_received": "Client information response received",
    "information_response_reviewed": "Client information response reviewed",
}


def overview(queryset):
    contacts = ClientContact.objects.filter(client_id=OuterRef("pk"), archived_at__isnull=True)
    tasks = ClientFollowUp.objects.filter(client_id=OuterRef("pk"), completed_at__isnull=True,
                                         canceled_at__isnull=True).order_by("due_date", "created_at", "id")
    jobs = WorkItem.objects.active().filter(client_contact__client_id=OuterRef("pk"),
                                            followup_date__isnull=False).order_by("followup_date", "queue_order", "id")
    return queryset.annotate(
        has_email=Exists(contacts.exclude(email__isnull=True).exclude(email="")),
        has_phone=Exists(contacts.exclude(phone="")),
        has_followup=Exists(tasks), has_job_followup=Exists(jobs),
        due_followup=Exists(tasks.filter(due_date__lte=timezone.localdate())),
        due_job_followup=Exists(jobs.filter(followup_date__lte=timezone.localdate())),
        next_task_date=Subquery(tasks.values("due_date")[:1]),
        next_task_title=Subquery(tasks.values("title")[:1]),
        next_job_date=Subquery(jobs.values("followup_date")[:1]),
        next_job_reference=Subquery(jobs.values("reference")[:1]))


def next_action(client):
    if client.next_task_date and (not client.next_job_date or client.next_task_date <= client.next_job_date):
        return {"title": client.next_task_title, "date": client.next_task_date,
                "due": client.next_task_date <= timezone.localdate()}
    if client.next_job_date:
        return {"title": f"Job follow-up: {client.next_job_reference}", "date": client.next_job_date,
                "due": client.next_job_date <= timezone.localdate()}
    return None


def possible_match_ids():
    """Same conservative identity keys as possible_matches; no automatic merge."""
    groups = defaultdict(set)
    for pk, name in Client.objects.filter(merged_into__isnull=True, kind="company").values_list("pk", "normalized_name"):
        if name:
            groups[("company", name)].add(pk)
    rows = ClientContact.objects.filter(archived_at__isnull=True, client__merged_into__isnull=True).values_list(
        "client_id", "normalized_name", "normalized_phone", "normalized_company")
    for pk, name, phone, company in rows:
        for kind, value in (("name", name), ("phone", phone), ("company", company)):
            if value:
                groups[(kind, value)].add(pk)
    return set().union(*(ids for ids in groups.values() if len(ids) > 1)) if groups else set()


def client_emails(client):
    emails = set(client.contacts.values_list("email", flat=True))
    emails.update(client.contacts.values_list("archived_email", flat=True))
    emails.update(WorkItem.objects.filter(client_contact__client=client).values_list("contact_email", flat=True))
    return sorted(email for email in emails if email)


def client_appointments(client):
    """Project association wins over a mailbox shared by multiple companies.

    Unassigned appointments remain discoverable by contact email, but the UI
    labels them explicitly rather than claiming they belong to this company.
    """
    projects = WorkItem.objects.filter(client_contact__client=client).exclude(
        project__isnull=True).values("project_id")
    return Appointment.objects.filter(
        Q(project_id__in=projects) | Q(project__isnull=True, email__in=client_emails(client)))


def timeline(client, page_number):
    visible = {"work_item__client_contact__client": client, "work_item__deleted_at__isnull": True}
    appointments = client_appointments(client)
    profile_ids, frontier = {client.pk}, {client.pk}
    while frontier:
        frontier = set(Client.objects.filter(merged_into_id__in=frontier).values_list("pk", flat=True)) - profile_ids
        profile_ids.update(frontier)
    notices = (Q(submission__work_item__client_contact__client=client, submission__work_item__deleted_at__isnull=True)
        | Q(status_milestone__work_item__client_contact__client=client, status_milestone__work_item__deleted_at__isnull=True)
        | Q(information_request__work_item__client_contact__client=client, information_request__work_item__deleted_at__isnull=True)
        | Q(information_reminder__work_item__client_contact__client=client, information_reminder__work_item__deleted_at__isnull=True)
        | Q(completed_delivery__work_item__client_contact__client=client, completed_delivery__work_item__deleted_at__isnull=True)
        | Q(appointment__in=appointments))
    sources = {
        "client": (ClientEvent.objects.filter(client_id__in=profile_ids).exclude(action__in=["Request added", "Client email prepared"]).select_related("actor"), "created_at"),
        "correspondence": (ClientEmail.objects.filter(client_id__in=profile_ids).select_related('delivery', 'project', 'actor'), 'created_at'),
        "submission": (Submission.objects.filter(**visible).select_related("work_item").prefetch_related("attachments"), "received_at"),
        "delivery": (CompletedDelivery.objects.filter(**visible).select_related("work_item").prefetch_related("files"), "created_at"),
        "appointment": (appointments.select_related("project"), "created_at"),
        "job": (AuditEvent.objects.filter(**visible).exclude(action="created").select_related("work_item", "actor"), "created_at"),
        "email": (NotificationDelivery.objects.filter(notices).annotate(email_date=Coalesce("sent_at", "created_at")), "email_date"),
    }
    indexes = []
    for kind, (queryset, date_field) in sources.items():
        indexes.append(queryset.order_by().annotate(occurred_at=F(date_field), category=Value(kind),
            record_key=Cast("pk", CharField())).values("occurred_at", "category", "record_key"))
    combined = indexes[0].union(*indexes[1:]).order_by("-occurred_at", "category", "record_key")
    page = Paginator(combined, 25).get_page(page_number)
    keys = defaultdict(list)
    for entry in page.object_list:
        keys[entry["category"]].append(entry["record_key"])
    records = {kind: {str(obj.pk).replace("-", ""): obj for obj in queryset.filter(pk__in=keys[kind])}
               for kind, (queryset, _) in sources.items()}
    result = []
    for entry in page.object_list:
        kind = entry["category"]
        obj = records[kind].get(entry["record_key"].replace("-", ""))
        if obj is None:
            continue  # A concurrent delete may remove a job from the visible scope.
        title, body, files, link = "", "", [], ""
        if kind == "client":
            title, body = obj.action, obj.changes.get("note", "")
            if not body and obj.changes.get("title"):
                body = obj.changes["title"]
        elif kind == "submission":
            title = f"{'Work entered manually' if obj.channel == 'staff' else 'Work submitted'} · {obj.work_item.reference}"
            body = obj.work_item.project_name
            files = [(file.original_name, reverse("workqueue:download", args=[file.pk])
                      if file.storage_key or file.legacy_drive_id else "") for file in obj.attachments.all()]
        elif kind == "delivery":
            title, body = f"Completed files prepared · {obj.work_item.reference}", obj.message
            files = [(file.original_name, reverse("workqueue:completed_file", args=[file.pk])) for file in obj.files.all()]
        elif kind == "appointment":
            title = f"Appointment · {obj.get_state_display()}"
            body = f"{timezone.localtime(obj.starts_at):%b %d, %Y at %I:%M %p} · {obj.purpose}"
            body += f" · {obj.project.name}" if obj.project_id else " · No project assigned; matched by contact email"
        elif kind == "job":
            title = f"{WORK_ACTIVITY_LABELS.get(obj.action, 'Work updated')} · {obj.work_item.reference}"
            status = obj.changes.get("status")
            if isinstance(status, dict):
                labels = dict(Status.choices)
                before, after = status.get("before"), status.get("after")
                if before in labels and after in labels:
                    body = f"Status: {labels[before]} → {labels[after]}"
        elif kind == 'correspondence':
            title = f'Client email · {obj.delivery.get_state_display()}'
            body = obj.subject
            link = reverse('workqueue:email_detail', args=[obj.pk])
        else:
            title = f"{obj.get_recipient_kind_display()} email · {obj.get_state_display()}"
            body = obj.subject
        if kind in {"submission", "delivery", "job"}:
            link = reverse("workqueue:queue") + f"?scope=all&q={obj.work_item.reference}#request-{obj.work_item_id}"
        result.append({"date": entry["occurred_at"], "title": title, "body": body, "files": files, "link": link})
    page.object_list = result
    return page
