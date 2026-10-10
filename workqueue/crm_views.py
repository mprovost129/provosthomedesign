from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Prefetch, Q
from django import forms
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .crm import archive_contact, restore_contact, create_client, create_client_work, edit_client, merge_clients, possible_matches, save_contact, undo_merge
from .crm_forms import ClientForm, ClientWorkForm, NewClientForm, ContactForm, ContactStateForm, MergeForm, UndoForm
from .models import Appointment, Client, ClientContact, ClientEvent, CLOSED_STATUSES, PendingUpload, WorkItem, WorkProject
from .access import new_intake_token, read_intake_token, session_digest
from .services import EditConflict
from .views import queue_enabled, staff_preview


def client_counts(queryset):
    return queryset.annotate(request_count=Count("contacts__work_items", distinct=True),
        active_count=Count("contacts__work_items", filter=~Q(contacts__work_items__status__in=CLOSED_STATUSES), distinct=True),
        last_request=Max("contacts__work_items__received_at")).prefetch_related(
            Prefetch("contacts", queryset=ClientContact.objects.filter(archived_at__isnull=True)))


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required("workqueue.view_workitem", raise_exception=True)
@never_cache
@require_http_methods(["GET"])
def clients(request):
    query = request.GET.get("q", "")[:200].strip()
    scope = request.GET.get("scope", "all")
    queryset = Client.objects.filter(merged_into__isnull=True)
    if query:
        matching = Client.objects.filter(Q(name__icontains=query) | Q(contacts__email__icontains=query)
            | Q(contacts__full_name__icontains=query) | Q(contacts__phone__icontains=query)
            | Q(contacts__work_items__reference__icontains=query)
            | Q(contacts__work_items__project__name__icontains=query)
            | Q(contacts__work_items__project_street__icontains=query)).values("pk")
        queryset = queryset.filter(pk__in=matching)
    queryset = client_counts(queryset)
    if scope == "repeat":
        queryset = queryset.filter(request_count__gt=1)
    elif scope == "active":
        queryset = queryset.filter(active_count__gt=0)
    else:
        scope = "all"
    page = Paginator(queryset.order_by("name", "id"), 30).get_page(request.GET.get("page"))
    return render(request, "workqueue/clients.html", {"page": page, "q": query, "scope": scope,
        "page_query": urlencode({"q": query, "scope": scope}),
        "client_count": Client.objects.filter(merged_into__isnull=True).count(),
        "can_add_client": not staff_preview(request) and request.user.has_perm("workqueue.change_workitem")})


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required(("workqueue.view_workitem", "workqueue.change_workitem"), raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def add_client(request):
    if staff_preview(request):
        raise PermissionDenied
    form = NewClientForm(request.POST or None, initial={"intake_token": new_intake_token(request)})
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                client, created = create_client(data=form.cleaned_data,
                    nonce=read_intake_token(request, form.cleaned_data["intake_token"]), actor=request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Client added. You can now add their work." if created else
                    "This client is already recorded. Opened the existing record without creating a duplicate.")
                return redirect("workqueue:client_detail", client.pk)
    return render(request, "workqueue/add_client.html", {"form": form}, status=status)


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required(("workqueue.view_workitem", "workqueue.add_workitem"), raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def add_client_work(request, client_id):
    if staff_preview(request):
        raise PermissionDenied
    client = get_object_or_404(Client, pk=client_id, merged_into__isnull=True)
    first_contact = client.contacts.filter(archived_at__isnull=True).first()
    form = ClientWorkForm(request.POST or None, client=client, initial={"intake_token": new_intake_token(request),
        "version": client.version, "kind": "new", "contact": first_contact})
    status, ready = 200, []
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                nonce = read_intake_token(request, form.cleaned_data["intake_token"])
                item, created = create_client_work(client_id=client.pk, version=form.cleaned_data["version"],
                    data=form.cleaned_data, nonce=nonce, binding=session_digest(request), actor=request.user)
            except (ValidationError, EditConflict) as exc:
                form.add_error(None, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                status = 409 if isinstance(exc, EditConflict) else 400
            else:
                messages.success(request, f"{item.reference} added to this client and the work queue." if created else
                    f"{item.reference} was already saved. No duplicate job was created.")
                return redirect("workqueue:client_detail", item.client_contact.client_id)
    try:
        nonce = read_intake_token(request, form.data.get("intake_token") if form.is_bound else form.initial["intake_token"])
        ready = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=session_digest(request),
            state="ready", expires_at__gt=timezone.now())
    except ValidationError:
        pass
    return render(request, "workqueue/add_client_work.html", {"form": form, "crm_client": client,
        "ready_uploads": ready, "direct_uploads": settings.INTAKE_DIRECT_UPLOADS,
        "intake_enabled": settings.WORK_INTAKE_ENABLED}, status=status)


@queue_enabled
@staff_member_required(login_url="admin:login")
@permission_required("workqueue.view_workitem", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def client_detail(request, client_id):
    client = get_object_or_404(Client.objects.select_related("merged_into").prefetch_related("contacts"), pk=client_id)
    can_change = not staff_preview(request) and request.user.has_perm("workqueue.change_workitem") and not client.merged_into_id
    edit_form = ClientForm(instance=client, initial={"version": client.version}, prefix="client")
    add_form = ContactForm(initial={"version": client.version}, prefix="contact")
    merge_form = MergeForm(client=client, initial={"version": client.version}, prefix="merge")
    preview = None
    signature = ""
    status = 200
    bound_contact_id = None
    bound_contact_form = None
    bound_copy_id = None
    bound_copy_form = None
    if request.method == "POST":
        if not can_change:
            raise PermissionDenied
        action = request.POST.get("action")
        try:
            if action == "edit":
                edit_form = ClientForm(request.POST, instance=client, prefix="client")
                if edit_form.is_valid():
                    edit_client(client_id=client_id, version=edit_form.cleaned_data["version"], actor=request.user,
                        data={key: edit_form.cleaned_data[key] for key in ClientForm.Meta.fields})
                    messages.success(request, "Client details saved. Original submissions remain unchanged.")
                    return redirect("workqueue:client_detail", client_id)
            elif action == "contact":
                bound_contact_id = forms.UUIDField(required=False).clean(request.POST.get("contact_key"))
                contact_form = ContactForm(request.POST, prefix=f"contact-{bound_contact_id}" if bound_contact_id else "contact")
                if bound_contact_id:
                    bound_contact_form = contact_form
                else:
                    add_form = contact_form
                if contact_form.is_valid():
                    if contact_form.cleaned_data["contact_id"] != bound_contact_id:
                        raise ValidationError("Reload this contact before editing it.")
                    contact_data = dict(contact_form.cleaned_data)
                    if contact_form.add_prefix("email") not in request.POST:
                        contact_data.pop("email", None)
                    save_contact(client_id=client_id, version=contact_form.cleaned_data["version"],
                        contact_id=bound_contact_id, data=contact_data, actor=request.user)
                    messages.success(request, "Contact saved.")
                    return redirect("workqueue:client_detail", client_id)
            elif action == "copy_contact":
                bound_copy_id = forms.UUIDField().clean(request.POST.get("contact_key"))
                bound_copy_form = ContactForm(request.POST, prefix=f"copy-{bound_copy_id}")
                if bound_copy_form.is_valid():
                    if bound_copy_form.cleaned_data["contact_id"]:
                        raise ValidationError("Copy creates a new contact; reload before trying again.")
                    save_contact(client_id=client_id, version=bound_copy_form.cleaned_data["version"],
                        contact_id=None, copy_from_id=bound_copy_id, data=bound_copy_form.cleaned_data, actor=request.user)
                    messages.success(request, "Contact copied. Jobs and files remain with the original contact.")
                    return redirect("workqueue:client_detail", client_id)
            elif action in {"delete_contact", "restore_contact"}:
                state_form = ContactStateForm(request.POST)
                if not state_form.is_valid():
                    raise ValidationError("Reload this client before changing the contact.")
                args = {"client_id": client_id, "version": state_form.cleaned_data["version"],
                        "contact_id": state_form.cleaned_data["contact_id"], "actor": request.user}
                if action == "delete_contact":
                    archive_contact(**args)
                    messages.success(request, "Contact deleted from the active list. Jobs and history are preserved; you can restore it below.")
                else:
                    restore_contact(**args, restore_email=state_form.cleaned_data["restore_email"])
                    messages.success(request, "Contact restored.")
                return redirect("workqueue:client_detail", client_id)
            elif action in {"preview_merge", "merge"}:
                merge_form = MergeForm(request.POST, client=client, prefix="merge")
                if merge_form.is_valid():
                    target = merge_form.cleaned_data["target"]
                    preview = {"source": str(client_id), "source_version": merge_form.cleaned_data["version"],
                        "target": str(target.pk), "target_version": target.version, "actor": request.user.pk}
                    if client.version != preview["source_version"]:
                        raise EditConflict("This client changed. Reload before previewing the merge.")
                    if action == "merge":
                        try:
                            approved = signing.loads(request.POST.get("signature", ""), salt="workqueue.crm.merge.v1", max_age=1800)
                        except signing.BadSignature:
                            raise ValidationError("Preview the merge again before confirming it.") from None
                        if approved != preview:
                            raise EditConflict("The merge preview changed or expired. Review it again.")
                        kept = merge_clients(source_id=client_id, source_version=preview["source_version"],
                            target_id=target.pk, target_version=target.version, actor=request.user)
                        messages.success(request, "Client records connected. Queue order and client access are unchanged.")
                        return redirect("workqueue:client_detail", kept.pk)
                    signature = signing.dumps(preview, salt="workqueue.crm.merge.v1")
                    preview = {"target": target, "contact_count": client.contacts.count(),
                               "request_count": WorkItem.objects.filter(client_contact__client=client).count()}
            elif action == "undo_merge":
                form = UndoForm(request.POST)
                if not form.is_valid():
                    raise ValidationError("Reload before reversing this merge.")
                source = undo_merge(client_id=client_id, version=form.cleaned_data["version"],
                                    event_id=form.cleaned_data["event_id"], actor=request.user)
                messages.success(request, "Merge reversed. The original client records are separate again.")
                return redirect("workqueue:client_detail", source.pk)
            else:
                raise ValidationError("Choose a supported client action.")
        except (ValidationError, EditConflict) as exc:
            error = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, error)
            if action == "contact":
                (bound_contact_form if bound_contact_form is not None else add_form).add_error(None, error)
            elif action == "copy_contact" and bound_copy_form is not None:
                bound_copy_form.add_error(None, error)
            status = 409 if isinstance(exc, EditConflict) else 400
        # ModelForm may mutate its instance; render current stored values separately.
        client.refresh_from_db()
        if status == 200 and not preview:
            status = 400
    all_contacts = list(client.contacts.all())
    contacts = [contact for contact in all_contacts if not contact.archived_at]
    items = WorkItem.objects.filter(client_contact__client=client).with_position().select_related("project", "client_contact").prefetch_related(
        "submissions__attachments", "submissions__notifications", "completed_deliveries__files", "milestones__delivery",
        "information_requests__delivery").order_by("-received_at", "-id")
    request_count, active_count = items.count(), items.active().count()
    projects = WorkProject.objects.filter(work_items__client_contact__client=client).annotate(
        job_count=Count("work_items", distinct=True),
        active_jobs=Count("work_items", filter=~Q(work_items__status__in=CLOSED_STATUSES), distinct=True),
        latest_job=Max("work_items__received_at")).order_by("-latest_job", "name")
    project_filter = request.GET.get("project", "")
    unlinked_count = items.filter(project__isnull=True).count()
    if project_filter == "unlinked":
        items = items.filter(project__isnull=True)
    elif project_filter:
        try:
            project_id = forms.UUIDField().clean(project_filter)
        except ValidationError:
            project_filter = ""
        else:
            if projects.filter(pk=project_id).exists():
                items = items.filter(project_id=project_id)
            else:
                project_filter = ""
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    emails = [contact.email for contact in contacts if contact.email]
    events = list(client.events.select_related("actor")[:30])
    reversible = next((event for event in events if event.action == "Client records merged"
                      and event.changes.get("target_version") == client.version), None)
    return render(request, "workqueue/client_detail.html", {"crm_client": client, "contacts": contacts,
        "page": page, "request_count": request_count, "active_count": active_count,
        "projects": projects, "project_filter": project_filter, "page_query": urlencode({"project": project_filter}),
        "unlinked_count": unlinked_count,
        "can_add_work": not staff_preview(request) and request.user.has_perm("workqueue.add_workitem") and not client.merged_into_id,
        "appointments": Appointment.objects.filter(email__in=emails).select_related("project").order_by("-starts_at")[:30],
        "events": events, "reversible": reversible, "can_change": can_change, "edit_form": edit_form,
        "archives": client.merged_clients.all(),
        "deleted_contacts": [contact for contact in all_contacts if contact.archived_at],
        "add_form": add_form, "contact_forms": [(contact, bound_contact_form if contact.pk == bound_contact_id else ContactForm(prefix=f"contact-{contact.pk}", initial={
            "version": client.version, "contact_id": contact.pk, "full_name": contact.full_name, "phone": contact.phone, "email": contact.email}),
            bound_copy_form if contact.pk == bound_copy_id else ContactForm(prefix=f"copy-{contact.pk}", initial={
                "version": client.version, "full_name": contact.full_name, "phone": contact.phone})) for contact in contacts],
        "merge_form": merge_form, "merge_preview": preview, "merge_signature": signature,
        "matches": possible_matches(client) if not client.merged_into_id else []}, status=status)
