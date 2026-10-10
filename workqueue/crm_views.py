from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django import forms
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .crm import edit_client, merge_clients, possible_matches, save_contact, undo_merge
from .crm_forms import ClientForm, ContactForm, MergeForm, UndoForm
from .models import Appointment, Client, ClientEvent, CLOSED_STATUSES, WorkItem
from .services import EditConflict
from .views import queue_enabled, staff_preview


def client_counts(queryset):
    return queryset.annotate(request_count=Count("contacts__work_items", distinct=True),
        active_count=Count("contacts__work_items", filter=~Q(contacts__work_items__status__in=CLOSED_STATUSES), distinct=True),
        last_request=Max("contacts__work_items__received_at")).prefetch_related("contacts")


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
        "client_count": Client.objects.filter(merged_into__isnull=True).count()})


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
                    save_contact(client_id=client_id, version=contact_form.cleaned_data["version"],
                        contact_id=bound_contact_id, data=contact_form.cleaned_data, actor=request.user)
                    messages.success(request, "Contact saved.")
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
            messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            status = 409 if isinstance(exc, EditConflict) else 400
        # ModelForm may mutate its instance; render current stored values separately.
        client.refresh_from_db()
        if status == 200 and not preview:
            status = 400
    contacts = list(client.contacts.all())
    items = WorkItem.objects.filter(client_contact__client=client).with_position().select_related("project", "client_contact").prefetch_related(
        "submissions__attachments", "submissions__notifications", "completed_deliveries__files", "milestones__delivery",
        "information_requests__delivery").order_by("-received_at", "-id")
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    emails = [contact.email for contact in contacts if contact.email]
    events = list(client.events.select_related("actor")[:30])
    reversible = next((event for event in events if event.action == "Client records merged"
                      and event.changes.get("target_version") == client.version), None)
    return render(request, "workqueue/client_detail.html", {"crm_client": client, "contacts": contacts,
        "page": page, "request_count": items.count(), "active_count": items.active().count(),
        "appointments": Appointment.objects.filter(email__in=emails).select_related("project").order_by("-starts_at")[:30],
        "events": events, "reversible": reversible, "can_change": can_change, "edit_form": edit_form,
        "archives": client.merged_clients.all(),
        "add_form": add_form, "contact_forms": [(contact, bound_contact_form if contact.pk == bound_contact_id else ContactForm(prefix=f"contact-{contact.pk}", initial={
            "version": client.version, "contact_id": contact.pk, "full_name": contact.full_name, "phone": contact.phone})) for contact in contacts],
        "merge_form": merge_form, "merge_preview": preview, "merge_signature": signature,
        "matches": possible_matches(client) if not client.merged_into_id else []}, status=status)
