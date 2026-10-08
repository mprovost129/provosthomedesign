"""Link verified replies conservatively; queue positions and statuses never change."""
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, InformationRequest, InformationResponse, Status, WorkItem
from .services import EditConflict, lock_queue


@transaction.atomic
def connect_information_responses(verified_email):
    from .access import authorized_work, normalize_email
    email = normalize_email(verified_email)
    if not email:
        return 0
    lock_queue()
    allowed = authorized_work(email)
    updates = WorkItem.objects.filter(pk__in=allowed.values("pk"), kind="update", project__isnull=False,
        contact_email__iexact=email, submissions__channel="website", submissions__owner_email=email,
        information_response__isnull=True).distinct().order_by("received_at", "pk")
    connected = 0
    for update in updates:
        requests = InformationRequest.objects.filter(work_item__project_id=update.project_id,
            work_item_id__in=allowed.values("pk"), delivery__recipient__iexact=email,
            created_at__lte=update.received_at).exclude(work_item_id=update.pk)
        explicit_reference = update.project_context.strip().upper()
        if update.previous_request_id:
            requests = requests.filter(work_item_id=update.previous_request_id)
        elif re.fullmatch(r"PHD-\d+", explicit_reference):
            requests = requests.filter(work_item__reference=explicit_reference)
        else:
            # A name/address identifies a project, not necessarily a particular request.
            requests = requests.filter(work_item__status=Status.NEEDS_INFORMATION)
            originals = list(requests.order_by().values_list("work_item_id", flat=True).distinct()[:2])
            if len(originals) != 1:
                continue
        question = requests.order_by("-created_at", "-pk").first()
        if question is None:
            continue
        # Later ordinary revisions must not reopen an already-reviewed exchange.
        if question.responses.filter(reviewed_at__lte=update.received_at).exists():
            continue
        response = InformationResponse.objects.create(information_request=question, work_item=update)
        original = WorkItem.objects.select_for_update().get(pk=question.work_item_id)
        original.version += 1
        original.save(update_fields=["version", "updated_at"])
        AuditEvent.objects.create(work_item=original, actor=None, action="information_response_received",
            changes={"response_id": response.pk, "update_reference": update.reference,
                     "information_request_id": question.pk})
        connected += 1
    return connected


@transaction.atomic
def review_information_response(*, item_id, response_id, version, actor):
    lock_queue()
    item = WorkItem.objects.select_for_update().get(pk=item_id)
    eligible = InformationResponse.objects.filter(information_request__work_item_id=item.pk).values("pk")
    response = InformationResponse.objects.select_for_update().filter(pk=response_id, pk__in=eligible).first()
    if response is None:
        raise ValidationError("This response does not belong to this request.")
    if response.reviewed_at:
        return item, False
    if item.version != version:
        raise EditConflict("This request changed. Review the latest response before marking it reviewed.")
    response.reviewed_at = timezone.now()
    response.reviewed_by = actor
    response.save(update_fields=["reviewed_at", "reviewed_by"])
    item.version += 1
    item.save(update_fields=["version", "updated_at"])
    AuditEvent.objects.create(work_item=item, actor=actor, action="information_response_reviewed",
        changes={"response_id": response.pk, "update_id": str(response.work_item_id)})
    return item, True
