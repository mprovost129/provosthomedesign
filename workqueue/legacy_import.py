"""Checked, repeatable pre-cutover import. Never sends mail or grants client access."""
import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from .models import (Attachment, AuditEvent, CLOSED_STATUSES, LegacyQueueImport,
                     Priority, Status, Submission, WorkItem, WorkProject)
from .services import lock_queue


class ImportConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def moment(value, zone):
    result = datetime.fromisoformat(text(value))
    if result.tzinfo is None:
        first, second = result.replace(tzinfo=zone, fold=0), result.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ImportConflict("A source timestamp needs an explicit UTC offset.")
        result = first
    return result


def day(value):
    return date.fromisoformat(text(value)[:10]) if value is not None and text(value) else None


def email(value):
    result = text(value).lower()
    if result:
        validate_email(result)
    return result


def positive(value):
    number = Decimal(text(value))
    if number != int(number) or number < 1:
        raise ImportConflict("Queue order must be a positive integer.")
    return int(number)


def deterministic_id(source, kind, key):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"google-sheet:{source}:{kind}:{key}")


def fingerprint(item):
    fields = {field.attname: str(getattr(item, field.attname)) for field in WorkItem._meta.fields
              if field.name not in {"updated_at"}}
    project = ({field.name: str(getattr(item.project, field.name)) for field in WorkProject._meta.fields}
               if item.project_id else None)
    submissions = []
    for sub in item.submissions.order_by("id"):
        files = [{field.attname: str(getattr(file, field.attname)) for field in Attachment._meta.fields}
                 for file in sub.attachments.order_by("id")]
        submissions.append({"id": str(sub.pk), "key": sub.idempotency_key, "channel": sub.channel,
                            "owner": sub.owner_email, "received": str(sub.received_at),
                            "answers": sub.answers, "files": files})
    return digest({"fields": fields, "project": project, "submissions": submissions})


FIELD_MAP = {
    "company": "Company", "contact_phone": "Phone", "project_name": "Project Name",
    "project_street": "Project Address", "project_city": "Project City",
    "project_state": "Project State", "project_zip": "Project ZIP",
    "service_needed": "Service Needed", "description": "Description of Work",
    "source": "Source", "internal_notes": "Internal Notes", "legacy_ref": "Legacy Reference",
}
DATE_MAP = {"requested_deadline": "Requested Deadline", "committed_due_date": "Committed Due Date",
            "scheduled_start": "Scheduled Start", "estimated_completion": "Estimated Completion",
            "followup_date": "Follow-up Date"}


def build_plan(payload, *, high_water, links=None):
    if payload.get("version") != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", payload.get("source_id", "")):
        raise ImportConflict("Unsupported source snapshot.")
    source, tables = payload["source_id"], payload["tables"]
    zone = ZoneInfo(payload["source_timezone"])
    rows = [r for r in tables["Master Queue"] if r.get("Queue ID")]
    status_map, priority_map = dict((label, value) for value, label in Status.choices), dict((label, value) for value, label in Priority.choices)
    ids, refs, orders = set(), set(), set()
    plans, warnings = [], []
    for row in rows:
        queue_id = str(uuid.UUID(row["Queue ID"]))
        reference, order = text(row["Request Number"]), positive(row["Queue Order"])
        if not re.fullmatch(r"PHD-\d{5,}", reference) or len(reference) > 32:
            raise ImportConflict("A queue reference is missing or invalid.")
        if queue_id in ids or reference in refs or order in orders:
            raise ImportConflict("Duplicate queue IDs, references or order values.")
        if row.get("Merged Into Queue ID"):
            raise ImportConflict("Merged queue rows require an explicit reconciliation plan.")
        ids.add(queue_id); refs.add(reference); orders.add(order)
        data = {field: text(row.get(header)) for field, header in FIELD_MAP.items()}
        data.update({field: day(row.get(header)) for field, header in DATE_MAP.items()})
        data.update(reference=reference, queue_order=order, received_at=moment(row["Received At"], zone),
                    contact_full_name=text(row.get("Contact Name")) or text(row.get("Client")),
                    contact_email=email(row.get("Email")), status=status_map[row["Status"]],
                    priority=priority_map[row["Priority"]],
                    kind="update" if row.get("Intake Channel") == "Files and Updates" else "new",
                    project_context=text(row.get("Project Address")) or text(row.get("Project Name")),
                    estimated_days=Decimal(text(row["Estimated Days"])) if row.get("Estimated Days") is not None else None)
        missing = [field for field in ["contact_full_name", "company", "contact_email", "contact_phone", "description"] if not data[field]]
        if missing:
            warnings.append({"reference": reference, "missing_historical_fields": missing})
        plans.append({"id": queue_id, "data": data, "queue_row": row, "submissions": []})
    by_id, by_ref = {r["id"]: r for r in plans}, {r["data"]["reference"]: r for r in plans}
    maximum = max([int(r[4:]) for r in refs] + [0])
    for row in tables["Submissions"]:
        ref = text(row.get("Request Number"))
        if re.fullmatch(r"PHD-\d{5,}", ref):
            maximum = max(maximum, int(ref[4:]))
    if high_water < maximum:
        raise ImportConflict("High-water mark is below an issued reference.")
    links = links or {}
    for child, parent in links.items():
        if child not in by_ref or parent not in by_ref or child == parent or parent in links:
            raise ImportConflict("Project connections must name distinct existing root requests.")
        by_ref[child]["root"] = by_ref[parent]["id"]
        by_ref[child]["data"]["kind"] = "update"
    answers_by_key, files_by_key, submitted = {}, {}, set()
    for answer in tables["Answers"]:
        if answer.get("Submission Key"):
            answers_by_key.setdefault(answer["Submission Key"], []).append(answer)
    for file in tables["Attachments"]:
        if file.get("Submission Key"):
            files_by_key.setdefault(file["Submission Key"], []).append(file)
    orphan_count = 0
    for row in tables["Submissions"]:
        key = row.get("Submission Key")
        if not key:
            continue
        if key in submitted:
            raise ImportConflict("Duplicate source submission keys.")
        submitted.add(key)
        queue_id = row.get("Queue ID")
        if queue_id not in by_id:
            orphan_count += 1
            continue  # retained losslessly in the private snapshot, never promoted to work
        if row.get("Process State") != "APPLIED":
            raise ImportConflict("A real request still has an unapplied submission.")
        owner = email(row.get("Contact Email")) or by_id[queue_id]["data"]["contact_email"]
        # An internal operator's verified Google identity is not the project client.
        spec = {"key": key, "row": row, "answers": answers_by_key.get(key, []),
                "files": files_by_key.get(key, []), "owner": owner,
                "received_at": moment(row["Submitted At"], zone)}
        by_id[queue_id]["submissions"].append(spec)
        if key == by_id[queue_id]["queue_row"].get("First Submission Key") and row.get("Requests Ahead") is not None:
            ahead = Decimal(text(row["Requests Ahead"]))
            if ahead < 0 or ahead != int(ahead):
                raise ImportConflict("Invalid historical receipt position.")
            by_id[queue_id]["data"]["submission_position"] = int(ahead) + 1
    for item in plans:
        if not item["submissions"]:
            item["submissions"] = [{"key": "legacy:" + item["id"], "row": {}, "answers": [], "files": [],
                                    "owner": item["data"]["contact_email"], "received_at": item["data"]["received_at"]}]
        item["source_hash"] = digest({"queue": item["queue_row"], "subs": item["submissions"],
                                      "root": item.get("root")})
    summary = {"requests": len(plans), "active": sum(r["data"]["status"] not in CLOSED_STATUSES for r in plans),
               "closed": sum(r["data"]["status"] in CLOSED_STATUSES for r in plans), "high_water": high_water,
               "last_queue_order": max(orders or [0]), "submissions": sum(len(r["submissions"]) for r in plans),
               "attachments": sum(len(s["files"]) for r in plans for s in r["submissions"]),
               "drive_files": sum(bool(f.get("File ID")) for r in plans for s in r["submissions"] for f in s["files"]),
               "external_links": sum(bool(f.get("External URL")) for r in plans for s in r["submissions"] for f in s["files"]),
               "archived_orphan_submissions": orphan_count, "connections": links,
               "missing_field_warnings": warnings}
    return source, zone, plans, summary


@transaction.atomic
def import_snapshot(payload, *, high_water, links=None, apply=False):
    if any(getattr(settings, flag, False) for flag in ("WORK_QUEUE_ENABLED", "WORK_INTAKE_ENABLED", "BOOKING_ENABLED")):
        raise ImportConflict("Disable all queue/intake/booking features before importing.")
    source, zone, plans, summary = build_plan(payload, high_water=high_water, links=links)
    state = lock_queue()
    current = list(WorkItem.objects.select_for_update().all())
    planned_ids = {r["id"] for r in plans}
    for item in current:
        event = item.audit_events.filter(action="legacy_imported").order_by("-id").first()
        if not event or event.changes.get("source_id") != source or str(item.pk) not in planned_ids:
            raise ImportConflict("Database contains work outside this source snapshot; reconcile it first.")
        if fingerprint(item) != event.changes.get("fingerprint"):
            raise ImportConflict("Imported work has been edited in the app; source overwrite stopped.")
    existing = {str(item.pk): item for item in current}
    changed = [r for r in plans if r["id"] not in existing or
               existing[r["id"]].audit_events.filter(action="legacy_imported").order_by("-id").first().changes["source_hash"] != r["source_hash"]]
    # A shared root change also changes the project's metadata for its updates.
    # Refresh every connected baseline together so the next reconciliation is safe.
    affected_roots = {r.get("root", r["id"]) for r in changed}
    changed = [r for r in plans if r.get("root", r["id"]) in affected_roots]
    items = {}
    for spec in plans:
        item = existing.get(spec["id"]) or WorkItem(id=spec["id"])
        for field, value in spec["data"].items():
            setattr(item, field, value)
        # Preserve genuine missing historical contact fields while validating all supplied data.
        excluded = [field.name for field in WorkItem._meta.fields if getattr(item, field.attname) == ""]
        item.full_clean(exclude=excluded)
        items[spec["id"]] = item
    # Validate every record before the first write; dry runs roll back their entire transaction.
    for spec in changed:
        item = items[spec["id"]]
        root_id = spec.get("root", spec["id"])
        root = items[root_id]
        project_id = deterministic_id(source, "project", root_id)
        project, _ = WorkProject.objects.update_or_create(id=project_id, defaults={
            "name": root.project_name or root.project_context or root.reference,
            "canonical_reference": root.reference, "street": root.project_street,
            "city": root.project_city, "state": root.project_state, "zip_code": root.project_zip,
            "created_at": root.received_at})
        item.project = project
        item.previous_request_id = root_id if root_id != spec["id"] else None
        # Foreign-key roots are saved first below.
    for spec in sorted(changed, key=lambda s: bool(s.get("root"))):
        item = items[spec["id"]]
        item.save()
        expected_sub_ids = set()
        for sub in spec["submissions"]:
            sub_id = deterministic_id(source, "submission", sub["key"])
            expected_sub_ids.add(sub_id)
            answers = {}
            for a in sub["answers"]:
                question = text(a.get("Question")) or text(a.get("Field Key"))
                if question in answers:
                    question += " [" + text(a.get("Item ID")) + "]"
                answers[question] = a.get("Answer Text")
            for label, value in spec["queue_row"].items():
                if value not in (None, ""):
                    answers[f"Original queue — {label}"] = value
            for i, file in enumerate(sub["files"], 1):
                if file.get("External URL"):
                    answers[f"Original file or folder link {i}"] = file["External URL"]
            record, _ = Submission.objects.update_or_create(id=sub_id, defaults={"work_item": item,
                "idempotency_key": f"sheet:{source}:{digest(sub['key'])}", "channel": "legacy_sheet",
                "owner_email": sub["owner"], "received_at": sub["received_at"], "answers": answers})
            expected_file_ids = set()
            for file in sub["files"]:
                drive_id = text(file.get("File ID"))
                if drive_id and not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", drive_id):
                    raise ImportConflict("Invalid historical Drive file identifier.")
                # External links are retained in the captured answers, never fetched automatically.
                if not drive_id:
                    continue
                file_id = deterministic_id(source, "attachment", file["Attachment Key"])
                expected_file_ids.add(file_id)
                Attachment.objects.update_or_create(id=file_id, defaults={"submission": record,
                    "original_name": text(file.get("File Name")) or "Google Drive attachment",
                    "legacy_drive_id": drive_id, "categories": [text(v) for v in text(file.get("Category")).split(";") if text(v)],
                    "uploaded_at": moment(file["Received At"], zone)})
            if record.attachments.exclude(id__in=expected_file_ids).exists():
                raise ImportConflict("An attachment disappeared from the source; reconciliation required.")
        if item.submissions.exclude(id__in=expected_sub_ids).exists():
            raise ImportConflict("A submission disappeared from the source; reconciliation required.")
    for spec in changed:
        item = WorkItem.objects.select_related("project").get(pk=spec["id"])
        AuditEvent.objects.create(work_item=item, action="legacy_imported", changes={"source_id": source,
            "source_hash": spec["source_hash"], "fingerprint": fingerprint(item)})
    state.last_reference_number = max(state.last_reference_number, high_water)
    state.last_queue_order = max(state.last_queue_order, summary["last_queue_order"])
    state.save(update_fields=["last_reference_number", "last_queue_order"])
    archive_digest = digest({"payload": payload, "high_water": high_water, "links": links or {}})
    LegacyQueueImport.objects.get_or_create(digest=archive_digest, defaults={"source_id": source,
        "payload": payload, "summary": summary})
    summary.update(changed_requests=len(changed), applied=apply, archive_digest=archive_digest)
    if not apply:
        transaction.set_rollback(True)
    return summary
