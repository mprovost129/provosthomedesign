"""Global attention counts and filters; positions still use the full active queue."""
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import CLOSED_STATUSES, InformationResponse, NotificationDelivery, Status, WorkItem


def attention_conditions():
    active = ~Q(status__in=CLOSED_STATUSES)
    today = timezone.localdate()
    return {
        "overdue": ("Overdue", active & Q(committed_due_date__lt=today)),
        "followup": ("Follow-ups due", active & Q(followup_date__lte=today)),
        "responses": ("Responses to review", Q(response_attention=True)),
        "information": ("Waiting for information", active & Q(status=Status.NEEDS_INFORMATION) & Q(response_attention=False)),
        "unlinked": ("Confirm project", active & Q(kind="update", project__isnull=True)),
        "email": ("Email issues", Q(email_attention=True)),
    }


def with_email_attention(items):
    issues = NotificationDelivery.objects.filter(state__in=("failed", "unknown")).filter(
        Q(submission__work_item_id=OuterRef("pk"))
        | Q(information_request__work_item_id=OuterRef("pk"))
        | Q(status_milestone__work_item_id=OuterRef("pk"))
    )
    replies = InformationResponse.objects.filter(information_request__work_item_id=OuterRef("pk"), reviewed_at__isnull=True)
    return items.annotate(email_attention=Exists(issues), response_attention=Exists(replies))


def attention_summary():
    items = with_email_attention(WorkItem.objects.all())
    return [{"key": key, "label": label, "count": items.filter(condition).count()}
            for key, (label, condition) in attention_conditions().items()]


def row_attention(item):
    if not item.is_active:
        return []
    today = timezone.localdate()
    flags = []
    if item.committed_due_date and item.committed_due_date < today:
        flags.append("Overdue")
    if item.followup_date and item.followup_date <= today:
        flags.append("Follow-up due")
    return flags
