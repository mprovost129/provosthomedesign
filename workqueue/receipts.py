"""Client receipts use the submitted answers, never mutable staff queue fields."""
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.dateparse import parse_datetime

from .client_forms import ClientIntakeForm
from .models import WorkItem

PRIVATE_FIELDS = {"intake_token", "upload_ids", "website"}


def answer_rows(submission, absolute_url):
    lines = []
    # Only public form questions may enter a client receipt. Retain the saved
    # answers rather than reconstructing them from a staff-edited WorkItem.
    for name, field in ClientIntakeForm.base_fields.items():
        if name in PRIVATE_FIELDS:
            continue
        label = field.label or name.replace("_", " ").capitalize()
        value = submission.answers.get(label, submission.answers.get(name))
        if value in (None, "", []):
            continue
        if name == "kind":
            value = dict(WorkItem.Kind.choices).get(value, value)
        elif name in {"terms_accepted", "same_address"}:
            value = "Yes" if value in (True, "True", "true", "on", "yes") else "No"
        elif isinstance(value, list):
            value = ", ".join(map(str, value))
        heading = label if label.endswith(("?", ".", "!", ":")) else label + ":"
        lines.append((heading, str(value)))
    terms = submission.answers.get("Terms URL")
    if terms:
        lines.append(("Terms & Conditions:", absolute_url(terms) if terms.startswith('/') else terms))
    accepted = submission.answers.get("Terms accepted at")
    if accepted:
        parsed = parse_datetime(accepted)
        if parsed and timezone.is_aware(parsed):
            accepted = date_format(timezone.localtime(parsed), "M j, Y, g:i A T")
        lines.append(("Terms accepted at:", str(accepted)))
    return lines


def submitted_answers(submission, absolute_url):
    return "\n\n".join(f"{label}\n{value}" for label, value in answer_rows(submission, absolute_url))


def submission_record(submission, absolute_url):
    when = date_format(timezone.localtime(submission.received_at), "M j, Y, g:i A T")
    answers = submitted_answers(submission, absolute_url)
    files = list(submission.attachments.order_by("uploaded_at", "pk"))
    sections = [f"YOUR SUBMISSION RECORD\nSubmitted: {when}\n\n{answers}",
                f"UPLOADED DOCUMENTS ({len(files)})"]
    if files:
        sections.append("Documents are linked, not attached to this email. Use the secure sign-in link above first, "
                        "then open a document link. Sign in with the email address used for this submission.")
        for index, attachment in enumerate(files, 1):
            entry = f"{index}. {attachment.original_name} ({filesizeformat(attachment.size_bytes)})"
            if attachment.categories:
                entry += "\nCategories: " + ", ".join(map(str, attachment.categories))
            if attachment.storage_key:
                entry += "\nOpen after signing in: " + absolute_url(reverse("workqueue:download", args=[attachment.pk]))
            else:
                entry += "\nRetained in the original intake archive. Contact us if you need a copy."
            sections.append(entry)
    else:
        sections.append("No files uploaded. Any file or folder link you provided is included in your answers above.")
    sections.append("Keep this email as a record of this submission. Later updates receive their own request ID and receipt.")
    return "\n\n".join(sections)
