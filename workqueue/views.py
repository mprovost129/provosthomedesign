import uuid
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ManualWorkForm, QueueEditForm, QuickStatusForm
from .models import AuditEvent, CLOSED_STATUSES, NotificationDelivery, Priority, Status, WorkItem
from .services import EDIT_FIELDS, EditConflict, change_project_access, create_work, edit_work


def queue_enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "WORK_QUEUE_ENABLED", False):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def filters_from(request):
    data = request.GET if request.method == "GET" else request.POST
    return {name: data.get(name, "")[:200] for name in ["q", "scope", "status", "priority", "page", "received_from", "received_to"]}


def queue_redirect(filters, item=None):
    url = reverse("workqueue:queue") + "?" + urlencode({key: value for key, value in filters.items() if value})
    return redirect(url + (f"#request-{item.pk}" if item else ""))


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required("workqueue.view_workitem", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def queue(request):
    filters = filters_from(request)
    error_item = None
    edit_form = None
    conflict_values = []
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
                        notice = get_object_or_404(NotificationDelivery.objects.select_for_update(),
                            pk=request.POST.get("notice_id"), submission__work_item=error_item)
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
                    item = edit_work(item_id=item_id, version=form.cleaned_data["version"], data=data, actor=request.user)
                except EditConflict as exc:
                    messages.error(request, str(exc))
                    response_status = 409
                    if edit_form:
                        conflict_values = [(edit_form.fields[name].label or name.replace('_', ' ').title(),
                                            request.POST.get(f"{error_item.pk}-{name}", ""))
                                           for name in EDIT_FIELDS]
                    edit_form = QueueEditForm(instance=WorkItem.objects.get(pk=item_id), prefix=str(item_id))
                except ValidationError as exc:
                    form.add_error(None, exc)
                    response_status = 400
                else:
                    messages.success(request, f"{item.reference} saved.")
                    return queue_redirect(filters, item)
            else:
                response_status = 400
            if action == "status":
                messages.error(request, "The status was not saved. Review the request and try again.")
            # Read a fresh instance: ModelForm validation can mutate its instance.
            error_item = WorkItem.objects.get(pk=item_id)
        else:
            raise Http404

    if request.method == "GET" or request.POST.get("action") != "create":
        create_form = ManualWorkForm(initial={"submission_token": uuid.uuid4()}, prefix="new")
    items = WorkItem.objects.with_position().select_related("project").prefetch_related(
        "submissions__attachments", "submissions__notifications",
        Prefetch("audit_events", queryset=AuditEvent.objects.select_related("actor")),
        Prefetch("project__work_items", queryset=WorkItem.objects.with_position()),
        "project__access_grants",
    )
    scope = filters["scope"] if filters["scope"] in ("active", "all", "closed") else "active"
    filters["scope"] = scope
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
    params = urlencode({key: value for key, value in filters.items() if key != "page" and value})
    from .models import Appointment, BookingAccess
    appointments = Appointment.objects.select_related("project").prefetch_related("notifications", "replacements").exclude(state__in=["canceled", "completed"]).order_by("starts_at")
    return render(request, "workqueue/queue.html", {"rows": rows, "page": page, "filters": filters,
        "appointments": appointments, "booking_access": BookingAccess.objects.order_by("email")[:100],
        "closed_appointments": Appointment.objects.filter(state__in=["canceled", "completed"]).prefetch_related("notifications").order_by("-starts_at")[:25],
        "can_manage_bookings": request.user.has_perm("workqueue.change_appointment"),
        "can_manage_booking_access": request.user.has_perm("workqueue.change_bookingaccess"),
        "page_query": params, "statuses": Status.choices, "priorities": Priority.choices,
        "create_form": create_form, "create_open": request.method == "POST" and request.POST.get("action") == "create",
        "can_add": request.user.has_perm("workqueue.add_workitem"),
        "can_change": request.user.has_perm("workqueue.change_workitem"),
        "can_manage_access": request.user.has_perm("workqueue.change_projectaccess"),
        "intake_enabled": getattr(settings, "WORK_INTAKE_ENABLED", False),
        "active_count": WorkItem.objects.active().count(), "closed_count": WorkItem.objects.filter(status__in=CLOSED_STATUSES).count()},
        status=response_status)
