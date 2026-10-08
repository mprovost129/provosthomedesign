import json
import uuid
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import InformationRequestForm, ManualWorkForm, QueueEditForm, QuickStatusForm
from .attention import attention_conditions, attention_summary, row_attention, with_email_attention
from .information_requests import INFORMATION_TEMPLATES, TEMPLATE_CHOICES, information_email, request_information
from .information_responses import review_information_response
from .models import AuditEvent, CLOSED_STATUSES, InformationResponse, NotificationDelivery, Priority, QueueState, Status, WorkItem
from .services import EDIT_FIELDS, EditConflict, change_project_access, create_work, edit_work


def staff_preview(request):
    return (not getattr(settings, "WORK_QUEUE_ENABLED", False)
            and getattr(settings, "WORK_QUEUE_STAFF_PREVIEW", False)
            and request.user.is_active and request.user.is_staff
            and request.user.has_perm("workqueue.view_workitem"))


def queue_enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "WORK_QUEUE_ENABLED", False) and not staff_preview(request):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def filters_from(request):
    data = request.GET if request.method == "GET" else request.POST
    prefix = "" if request.method == "GET" else "filter_"
    return {name: data.get(prefix + name, "")[:200] for name in ["q", "scope", "status", "priority", "page", "received_from", "received_to", "attention"]}


def information_review_data(item, form):
    return {"item": str(item.pk), "recipient": item.contact_email,
            "version": form.cleaned_data["version"], "token": str(form.cleaned_data["token"]),
            "message": form.cleaned_data["message"],
            "reminder_date": str(form.cleaned_data["reminder_date"]) if form.cleaned_data["reminder_date"] else None,
            "followup_date": str(form.cleaned_data["followup_date"]) if form.cleaned_data["followup_date"] else None}


def queue_redirect(filters, item=None):
    url = reverse("workqueue:queue") + "?" + urlencode({key: value for key, value in filters.items() if value})
    return redirect(url + (f"#request-{item.pk}" if item else ""))


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required("workqueue.view_workitem", raise_exception=True)
@never_cache
@require_http_methods(["GET"])
def arrivals(request):
    try:
        baseline = signing.loads(request.GET.get("snapshot", ""), salt="workqueue.arrivals.v1", max_age=604800)
        if not isinstance(baseline, int) or baseline < 0:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError):
        return JsonResponse({"error": "Refresh the queue to resume checking for new requests."}, status=400)
    # The allocator advances exactly once per committed request, including updates and staff entry.
    current = QueueState.objects.get(pk=1).last_reference_number
    return JsonResponse({"count": max(0, current - baseline)})


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required(("workqueue.view_workitem", "workqueue.change_workitem"), raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def ordering(request):
    from .ordering import order_snapshot, save_order
    if staff_preview(request):
        raise PermissionDenied
    if request.method == "GET":
        return JsonResponse(order_snapshot())
    try:
        if len(request.body) > 1024 * 1024:
            raise ValueError
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) != {"ordered_ids", "snapshot"}:
            raise ValueError
        if not isinstance(data["snapshot"], str):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid queue order."}, status=400)
    try:
        changed = save_order(ordered_ids=data["ordered_ids"], snapshot=data["snapshot"], actor=request.user)
    except EditConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse({"saved": True, "changed": changed})


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required("workqueue.view_workitem", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def queue(request):
    preview = staff_preview(request)
    if preview and request.method != "GET":
        raise PermissionDenied
    filters = filters_from(request)
    arrival_snapshot = signing.dumps(QueueState.objects.get(pk=1).last_reference_number, salt="workqueue.arrivals.v1")
    error_item = None
    edit_form = None
    conflict_values = []
    information_form = None
    information_preview = None
    information_signature = ""
    create_form = ManualWorkForm(initial={"submission_token": uuid.uuid4()}, prefix="new")
    response_status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("booking_grant", "booking_revoke", "booking_cancel", "booking_retry", "booking_retry_email"):
            if not getattr(settings, "BOOKING_ENABLED", False):
                raise Http404
            from .booking_views import staff_action
            return staff_action(request)
        if action == "create":
            if not request.user.has_perm("workqueue.add_workitem"):
                raise PermissionDenied
            create_form = ManualWorkForm(request.POST, prefix="new")
            if create_form.is_valid():
                data = {name: create_form.cleaned_data[name] for name in ManualWorkForm.Meta.fields}
                try:
                    item, created = create_work(data=data, actor=request.user,
                        idempotency_key=f"staff:{request.user.pk}:{create_form.cleaned_data['submission_token']}")
                except ValidationError as exc:
                    create_form.add_error(None, exc)
                else:
                    messages.success(request, f"{item.reference} {'added to the queue' if created else 'was already saved'}.")
                    return queue_redirect(filters, item)
            response_status = 400
        elif action == "review_information_response":
            if not request.user.has_perm("workqueue.change_workitem"):
                raise PermissionDenied
            try:
                item_id = uuid.UUID(request.POST.get("item_id", ""))
                response_id = int(request.POST.get("response_id", "0"))
                version = int(request.POST.get("version", "0"))
            except (ValueError, TypeError, AttributeError):
                raise Http404
            error_item = get_object_or_404(WorkItem, pk=item_id)
            try:
                item, changed = review_information_response(item_id=item_id, response_id=response_id,
                    version=version, actor=request.user)
            except (ValidationError, EditConflict) as exc:
                messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                response_status = 409 if isinstance(exc, EditConflict) else 400
            else:
                messages.success(request, f"{item.reference}: response " + ("marked reviewed. Job status is unchanged." if changed else "was already reviewed."))
                return queue_redirect(filters, item)
        elif action in ("preview_information", "send_information"):
            if not request.user.has_perm("workqueue.change_workitem"):
                raise PermissionDenied
            if not getattr(settings, "WORK_INTAKE_ENABLED", False):
                raise Http404
            try:
                item_id = uuid.UUID(request.POST.get("item_id", ""))
            except (ValueError, TypeError, AttributeError):
                raise Http404
            error_item = get_object_or_404(WorkItem, pk=item_id)
            information_form = InformationRequestForm(request.POST, prefix=f"information-{item_id}")
            if information_form.is_valid():
                try:
                    review_data = information_review_data(error_item, information_form)
                    if action == "send_information":
                        try:
                            reviewed = signing.loads(request.POST.get("preview_signature", ""),
                                salt="workqueue.information-review.v1", max_age=1800)
                        except signing.BadSignature:
                            raise ValidationError("Preview this email again before sending. Email previews expire after 30 minutes.") from None
                        if reviewed != review_data:
                            raise ValidationError("The email changed since its preview. Preview it again before sending.")
                        item, created = request_information(item_id=item_id,
                            version=information_form.cleaned_data["version"],
                            message=information_form.cleaned_data["message"],
                            followup_date=information_form.cleaned_data["followup_date"],
                            reminder_date=information_form.cleaned_data["reminder_date"],
                            token=information_form.cleaned_data["token"], actor=request.user)
                        messages.success(request, f"{item.reference}: " + ("email queued and marked Needs Information." if created else "this email was already queued; no second email was created."))
                        return queue_redirect(filters, item)
                    if error_item.version != information_form.cleaned_data["version"]:
                        raise EditConflict("This request changed in another tab. Review the current request and preview the email again.")
                    if not error_item.is_active:
                        raise ValidationError("Reopen this request before requesting more information.")
                    information_preview = information_email(error_item, information_form.cleaned_data["message"])
                    information_signature = signing.dumps(review_data, salt="workqueue.information-review.v1")
                except (ValidationError, EditConflict) as exc:
                    information_form.add_error(None, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                    response_status = 409 if isinstance(exc, EditConflict) else 400
                    # Keep the message; require a fresh preview against current request values.
                    if isinstance(exc, EditConflict):
                        corrected = information_form.data.copy()
                        corrected[f"information-{item_id}-version"] = str(WorkItem.objects.get(pk=item_id).version)
                        information_form = InformationRequestForm(corrected, prefix=f"information-{item_id}")
                        information_form.is_valid()
                        information_form.add_error(None, str(exc))
            else:
                response_status = 400
        elif action in ("access_grant", "access_revoke", "retry_notice"):
            required_permission = "workqueue.change_projectaccess" if action != "retry_notice" else "workqueue.change_workitem"
            if not request.user.has_perm(required_permission):
                raise PermissionDenied
            try:
                item_id = uuid.UUID(request.POST.get("item_id", ""))
                version = int(request.POST.get("version", "0"))
            except (ValueError, TypeError):
                raise Http404
            error_item = get_object_or_404(WorkItem, pk=item_id)
            try:
                if action == "retry_notice":
                    from django.db import transaction
                    with transaction.atomic():
                        eligible_notices = NotificationDelivery.objects.filter(
                            Q(submission__work_item=error_item) | Q(information_request__work_item=error_item)
                            | Q(status_milestone__work_item=error_item) | Q(completed_delivery__work_item=error_item)
                            | Q(information_reminder__work_item=error_item)).values("pk")
                        # Keep nullable receipt/message joins out of the locked outer query on PostgreSQL.
                        notice = get_object_or_404(NotificationDelivery.objects.select_for_update(),
                            pk=request.POST.get("notice_id"), pk__in=eligible_notices)
                        if notice.state not in ("failed", "unknown"):
                            raise ValidationError("This email cannot be retried in its current state.")
                        if notice.state == "unknown" and request.POST.get("not_sent_confirmed") != "yes":
                            raise ValidationError("Check your mail provider and confirm this email was not delivered before resending.")
                        notice.state = "pending"
                        notice.last_error = ""
                        notice.save(update_fields=["state", "last_error"])
                        AuditEvent.objects.create(work_item=error_item, actor=request.user, action="email_retry_requested",
                                                  changes={"delivery_id": notice.pk})
                else:
                    change_project_access(item_id=item_id, version=version, email=request.POST.get("access_email", ""),
                                          allow=action == "access_grant", actor=request.user)
            except (ValidationError, EditConflict) as exc:
                messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                response_status = 409 if isinstance(exc, EditConflict) else 400
            else:
                messages.success(request, "Project access saved." if action != "retry_notice" else "Email queued for another delivery attempt.")
                return queue_redirect(filters, error_item)
        elif action in ("edit", "status"):
            if not request.user.has_perm("workqueue.change_workitem"):
                raise PermissionDenied
            try:
                item_id = uuid.UUID(request.POST.get("item_id", ""))
            except (ValueError, TypeError, AttributeError):
                raise Http404
            error_item = get_object_or_404(WorkItem, pk=item_id)
            edit_form = QueueEditForm(request.POST, instance=error_item, prefix=str(error_item.pk)) if action == "edit" else None
            form = edit_form if action == "edit" else QuickStatusForm(request.POST)
            if form.is_valid():
                data = {name: form.cleaned_data[name] for name in EDIT_FIELDS} if action == "edit" else {"status": form.cleaned_data["status"]}
                try:
                    item = edit_work(item_id=item_id, version=form.cleaned_data["version"], data=data, actor=request.user,
                        notify_client=form.cleaned_data["client_email"] == "send")
                except EditConflict as exc:
                    messages.error(request, str(exc))
                    response_status = 409
                    if edit_form:
                        conflict_values = [(edit_form.fields[name].label or name.replace('_', ' ').title(),
                                            request.POST.get(f"{error_item.pk}-{name}", ""))
                                           for name in EDIT_FIELDS]
                    edit_form = QueueEditForm(instance=WorkItem.objects.get(pk=item_id), prefix=str(item_id))
                except ValidationError as exc:
                    # Model errors may refer to fields absent from the status/edit
                    # form. Flatten them into non-field errors instead of raising.
                    form.add_error(None, ValidationError(exc.messages))
                    response_status = 400
                else:
                    milestone = item.milestone_notification
                    suffix = (" Client email queued." if milestone.delivery_id else " Client email skipped.") if milestone else ""
                    messages.success(request, f"{item.reference} saved." + suffix)
                    return queue_redirect(filters, item)
            else:
                response_status = 400
            if action == "status":
                detail = " ".join(form.non_field_errors()) or "Review the request and try again."
                messages.error(request, "The status was not saved. " + detail)
            # Read a fresh instance: ModelForm validation can mutate its instance.
            error_item = WorkItem.objects.get(pk=item_id)
        else:
            raise Http404

    if request.method == "GET" or request.POST.get("action") != "create":
        create_form = ManualWorkForm(initial={"submission_token": uuid.uuid4()}, prefix="new")
    items = with_email_attention(WorkItem.objects.with_position()).select_related("project").prefetch_related(
        "submissions__attachments", "submissions__notifications",
        "information_requests__delivery", "information_requests__reminder_delivery",
        "milestones__delivery", "milestones__actor",
        "completed_deliveries__files", "completed_deliveries__notification",
        Prefetch("information_requests__responses", queryset=InformationResponse.objects.select_related("work_item", "reviewed_by").prefetch_related("work_item__submissions__attachments")),
        Prefetch("audit_events", queryset=AuditEvent.objects.select_related("actor")),
        Prefetch("project__work_items", queryset=WorkItem.objects.with_position()),
        "project__access_grants",
    )
    scope = filters["scope"] if filters["scope"] in ("active", "all", "closed") else "active"
    filters["scope"] = scope
    conditions = attention_conditions()
    if filters["attention"] in conditions:
        items = items.filter(conditions[filters["attention"]][1])
    else:
        filters["attention"] = ""
    if scope == "active":
        items = items.active()
    elif scope == "closed":
        items = items.filter(status__in=CLOSED_STATUSES)
    if filters["q"]:
        query = filters["q"]
        search = Q()
        for field in ["reference", "contact_full_name", "company", "contact_email", "project_name",
                      "project_context", "project_street", "project_city", "project_zip", "project__name",
                      "project__street", "project__city", "project__canonical_reference"]:
            search |= Q(**{f"{field}__icontains": query})
        items = items.filter(search)
    if filters["status"] in Status.values:
        items = items.filter(status=filters["status"])
    if filters["priority"] in Priority.values:
        items = items.filter(priority=filters["priority"])
    for name, lookup in [("received_from", "received_at__date__gte"), ("received_to", "received_at__date__lte")]:
        if filters[name]:
            try:
                date = forms.DateField().clean(filters[name])
            except ValidationError:
                messages.error(request, "Use a valid received date.")
                filters[name] = ""
            else:
                items = items.filter(**{lookup: date})
    page = Paginator(items, 25).get_page(filters["page"])
    rows = []
    for item in page:
        rows.append({"item": item, "form": edit_form if error_item and item.pk == error_item.pk and edit_form
                     else QueueEditForm(instance=item, prefix=str(item.pk)),
                     "open": bool(error_item and item.pk == error_item.pk),
                     "conflict_values": conflict_values if error_item and item.pk == error_item.pk else []})
    # A conflict or invalid form must stay visible even if the request moved outside this filter/page.
    if error_item and not any(row["item"].pk == error_item.pk for row in rows):
        error_item = WorkItem.objects.with_position().select_related("project").get(pk=error_item.pk)
        rows.insert(0, {"item": error_item, "form": edit_form or QueueEditForm(instance=error_item, prefix=str(error_item.pk)), "open": True, "conflict_values": conflict_values})
    for row in rows:
        item = row["item"]
        current = bool(error_item and item.pk == error_item.pk)
        row["attention_flags"] = row_attention(item)
        row["files"] = [file for submission in item.submissions.all() for file in submission.attachments.all()]
        row["responses"] = [response for note in item.information_requests.all() for response in note.responses.all()]
        row["unreviewed_responses"] = [response for response in row["responses"] if not response.reviewed_at]
        row["information_form"] = information_form if current and information_form else InformationRequestForm(
            prefix=f"information-{item.pk}", initial={"version": item.version, "token": uuid.uuid4(),
                "message": INFORMATION_TEMPLATES["documents"], "followup_date": item.followup_date})
        row["information_open"] = current and information_form is not None
        row["information_preview"] = information_preview if current else None
        row["information_signature"] = information_signature if current else ""
    params = urlencode({key: value for key, value in filters.items() if key != "page" and value})
    from .models import Appointment, BookingAccess
    from .health import health_snapshot
    appointments = Appointment.objects.select_related("project").prefetch_related("notifications", "replacements").exclude(state__in=["canceled", "completed"]).order_by("starts_at")
    return render(request, "workqueue/queue.html", {"rows": rows, "page": page, "filters": filters,
        "queue_health": health_snapshot(),
        "arrival_snapshot": arrival_snapshot,
        "appointments": appointments, "booking_access": BookingAccess.objects.order_by("email")[:100],
        "closed_appointments": Appointment.objects.filter(state__in=["canceled", "completed"]).prefetch_related("notifications").order_by("-starts_at")[:25],
        "can_manage_bookings": not preview and request.user.has_perm("workqueue.change_appointment"),
        "can_manage_booking_access": not preview and request.user.has_perm("workqueue.change_bookingaccess"),
        "page_query": params, "statuses": Status.choices, "priorities": Priority.choices,
        "create_form": create_form, "create_open": request.method == "POST" and request.POST.get("action") == "create",
        "can_add": not preview and request.user.has_perm("workqueue.add_workitem"),
        "can_change": not preview and request.user.has_perm("workqueue.change_workitem"),
        "can_manage_access": not preview and request.user.has_perm("workqueue.change_projectaccess"),
        "intake_enabled": not preview and getattr(settings, "WORK_INTAKE_ENABLED", False),
        "staff_preview": preview,
        "attention_summary": attention_summary(), "information_templates": INFORMATION_TEMPLATES,
        "information_template_choices": TEMPLATE_CHOICES,
        "active_count": WorkItem.objects.active().count(), "closed_count": WorkItem.objects.filter(status__in=CLOSED_STATUSES).count()},
        status=response_status)
