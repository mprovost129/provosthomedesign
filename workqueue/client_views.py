import logging
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import (authorized_work, consume_email_token, new_intake_token, normalize_email,
                     rate_allowed, read_intake_token, session_digest, verified_email)
from .client_forms import ClientIntakeForm, ConfirmAccessForm, TrackingAccessForm
from .intake import submit_intake
from .models import Attachment, PendingUpload, WorkItem
from .notifications import queue_signin
from .uploads import direct_upload_policy, finish_upload, private_storage, reserve_upload
from .views import queue_enabled

logger = logging.getLogger(__name__)


def intake_enabled(view):
    from functools import wraps
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "WORK_INTAKE_ENABLED", False):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def limited(request, scope, identity=None, limit=20, window=3600):
    return rate_allowed(scope, identity or request.META.get("REMOTE_ADDR", "unknown"), limit, window)


@intake_enabled
@never_cache
@require_http_methods(["GET", "POST"])
def submit_work(request):
    email = verified_email(request)
    initial = {"kind": "update" if request.GET.get("kind") == "update" else "new", "intake_token": new_intake_token(request)}
    if email:
        initial["contact_email"] = email
    form = ClientIntakeForm(request.POST or None, initial=initial, email=email)
    status = 200
    ready = []
    if request.method == "POST" and form.is_valid():
        try:
            nonce = read_intake_token(request, form.cleaned_data["intake_token"])
            if not limited(request, "submissions", limit=30):
                raise ValidationError("Too many submissions in a short time. Please try again later.")
            item, created = submit_intake(data=form.cleaned_data, nonce=nonce, binding=session_digest(request),
                verified_email=email, labels={name: field.label for name, field in form.fields.items()})
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 400
        else:
            allowed = request.session.get("queue_recent_requests", [])
            request.session["queue_recent_requests"] = (allowed + [str(item.pk)])[-20:]
            return redirect("workqueue:submitted", item_id=item.pk)
    elif request.method == "POST":
        status = 400
    # Keep successfully uploaded files on an ordinary field-validation retry.
    try:
        token = form.data.get("intake_token") if form.is_bound else form.initial["intake_token"]
        nonce = read_intake_token(request, token)
        ready = PendingUpload.objects.filter(intake_nonce=nonce, session_digest=session_digest(request),
                                             state="ready", expires_at__gt=timezone.now())
    except ValidationError:
        pass
    groups = {
        "common": [form[name] for name in ["contact_full_name", "company", "contact_email", "contact_phone"]],
        "billing": [form[name] for name in ["billing_street", "billing_city", "billing_state", "billing_zip"]],
        "address": [form[name] for name in ["project_street", "project_city", "project_state", "project_zip"]],
        "new": [form[name] for name in ["project_name", "service_needed", "new_description", "approximate_size", "timeframe", "referral_detail"]],
        "update": [form[name] for name in ["project", "project_reference", "project_context", "update_description"]],
        "shared": [form[name] for name in ["file_link", "requested_deadline", "plan_number", "preferred_contact", "notes"]],
        "detailed": [form[name] for name in ["home_preferences", "plan_changes", "framing_scope", "site_constraints", "project_contacts"]],
    }
    return render(request, "workqueue/submit.html", {"form": form, "groups": groups, "ready_uploads": ready,
        "is_update": form.data.get("kind") == "update" if form.is_bound else initial["kind"] == "update",
        "direct_uploads": settings.INTAKE_DIRECT_UPLOADS, "client_email": email}, status=status)


@intake_enabled
@never_cache
@require_http_methods(["POST"])
def start_upload(request):
    upload = None
    try:
        nonce = read_intake_token(request, request.POST.get("intake_token", ""))
        binding = session_digest(request)
        if not limited(request, "upload-reservations", limit=100):
            raise ValidationError("Too many upload attempts. Please try again later.")
        file = request.FILES.get("file")
        size = file.size if file else int(request.POST.get("size", "0"))
        name = file.name if file else request.POST.get("name", "")
        upload_id = uuid.UUID(request.POST["upload_id"]) if request.POST.get("upload_id") else None
        upload = reserve_upload(nonce=nonce, binding=binding, name=name, size=size, upload_id=upload_id)
        if upload.state == "ready":
            return JsonResponse({"id": str(upload.pk), "name": upload.original_name, "ready": True})
        if settings.INTAKE_DIRECT_UPLOADS:
            if file:
                raise ValidationError("Please use the direct upload control.")
            policy = direct_upload_policy(upload)
            return JsonResponse({"id": str(upload.pk), "policy": policy})
        if not file:
            raise ValidationError("Select a file to upload.")
        with transaction.atomic():
            upload = PendingUpload.objects.select_for_update().get(pk=upload.pk)
            if upload.state == "reserved":
                finish_upload(upload, local_file=file)
            elif upload.state != "ready":
                raise ValidationError("Start this file upload again.")
        return JsonResponse({"id": str(upload.pk), "name": upload.original_name, "ready": True})
    except (ValidationError, ValueError) as exc:
        if upload:
            PendingUpload.objects.filter(pk=upload.pk, state="reserved").update(state="failed")
        return JsonResponse({"error": " ".join(exc.messages) if isinstance(exc, ValidationError) else "The file size is invalid."}, status=400)
    except Exception:
        if upload:
            PendingUpload.objects.filter(pk=upload.pk, state="reserved").update(state="failed")
        logger.warning("Private upload initialization failed")
        return JsonResponse({"error": "Uploads are temporarily unavailable. Please retry, or provide a secure file link."}, status=503)


@intake_enabled
@never_cache
@require_http_methods(["POST"])
def upload_action(request, upload_id):
    try:
        nonce = read_intake_token(request, request.POST.get("intake_token", ""))
        with transaction.atomic():
            upload = get_object_or_404(PendingUpload.objects.select_for_update(), pk=upload_id,
                intake_nonce=nonce, session_digest=session_digest(request), expires_at__gt=timezone.now())
            if request.POST.get("action") == "remove":
                if upload.state == "attached":
                    raise Http404
                upload.state = "failed"
                upload.save(update_fields=["state"])
                return JsonResponse({"removed": True})
            if upload.state == "ready":
                return JsonResponse({"ready": True, "id": str(upload.pk), "name": upload.original_name})
            if upload.state != "reserved" or not settings.INTAKE_DIRECT_UPLOADS:
                raise ValidationError("Start this upload again.")
            finish_upload(upload)
            return JsonResponse({"ready": True, "id": str(upload.pk), "name": upload.original_name})
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    except Http404:
        raise
    except Exception:
        logger.warning("Private upload completion failed")
        return JsonResponse({"error": "That upload could not be completed. Please retry it."}, status=503)


@intake_enabled
@never_cache
@require_http_methods(["GET"])
def submitted(request, item_id):
    if str(item_id) not in request.session.get("queue_recent_requests", []):
        raise Http404
    item = get_object_or_404(WorkItem.objects.select_related("project"), pk=item_id)
    return render(request, "workqueue/submitted.html", {"item": item,
        "ahead": max(0, (item.submission_position or 1) - 1)})


@intake_enabled
@never_cache
@require_http_methods(["GET", "POST"])
def tracking(request):
    email = verified_email(request)
    form = TrackingAccessForm(request.POST or None, initial={"reference": request.GET.get("reference", "")})
    sent = False
    status = 200
    if request.method == "POST" and form.is_valid():
        target = normalize_email(form.cleaned_data["email"])
        if limited(request, "signin-ip", limit=20, window=900) and limited(request, "signin-email", target, limit=5, window=900):
            queue_signin(target, form.cleaned_data["reference"].strip().upper())
        # Identical response whether the email/reference matches any existing work.
        sent = True
    elif request.method == "POST":
        status = 400
    reference = request.GET.get("reference", "").strip().upper()[:32]
    items = authorized_work(email).with_position().select_related("project").prefetch_related("submissions__attachments")
    selected = items.filter(reference=reference).first() if reference else None
    unavailable = bool(email and reference and selected is None)
    if selected and selected.project_id:
        items = items.filter(project_id=selected.project_id)
    elif reference:
        items = items.filter(reference=reference)
    # Only client-facing information is passed to this template; internal notes are never rendered.
    return render(request, "workqueue/tracking.html", {"form": form, "sent": sent, "client_email": email,
        "items": items[:100], "selected": selected, "unavailable": unavailable, "checked_at": timezone.now()}, status=status)


@intake_enabled
@never_cache
@require_http_methods(["GET", "POST"])
def confirm_access(request):
    form = ConfirmAccessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            if not limited(request, "signin-consume", limit=20, window=900):
                raise ValidationError("Too many attempts. Please request a new sign-in link later.")
            record = consume_email_token(form.cleaned_data["token"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            try:
                from .access import reconcile_verified_updates
                reconcile_verified_updates(record.email)
            except Exception:
                logger.warning("Verified update linking needs owner review")
            request.session.cycle_key()
            request.session["queue_verified_email"] = record.email
            request.session["queue_email_verified_until"] = (timezone.now() + timedelta(days=7)).timestamp()
            target = reverse("workqueue:book" if record.destination == "booking" and getattr(settings, "BOOKING_ENABLED", False) else "workqueue:tracking")
            if record.reference:
                from urllib.parse import urlencode
                target += "?" + urlencode({"reference": record.reference})
            return redirect(target)
    return render(request, "workqueue/access_confirm.html", {"form": form}, status=400 if form.errors else 200)


@intake_enabled
@never_cache
@require_http_methods(["POST"])
def client_logout(request):
    request.session.pop("queue_verified_email", None)
    request.session.pop("queue_email_verified_until", None)
    request.session.cycle_key()
    return redirect("workqueue:tracking")


@never_cache
@require_http_methods(["GET"])
def download(request, attachment_id):
    if not getattr(settings, "WORK_QUEUE_ENABLED", False):
        raise Http404
    attachment = get_object_or_404(Attachment.objects.select_related("submission__work_item"), pk=attachment_id)
    staff = request.user.is_active and request.user.is_staff and request.user.has_perm("workqueue.view_workitem")
    if not staff:
        if not getattr(settings, "WORK_INTAKE_ENABLED", False) or not authorized_work(verified_email(request)).filter(pk=attachment.submission.work_item_id).exists():
            raise Http404
    if not attachment.storage_key:
        # Imported files stay in the owner's existing private Drive archive.
        # Staff can open them there; this never grants clients Google Drive access.
        if staff and re.fullmatch(r"[A-Za-z0-9_-]{10,200}", attachment.legacy_drive_id):
            return redirect(f"https://drive.google.com/file/d/{attachment.legacy_drive_id}/view")
        raise Http404
    try:
        file = private_storage().open(attachment.storage_key, "rb")
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response = FileResponse(file, as_attachment=True, filename=attachment.original_name, content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
