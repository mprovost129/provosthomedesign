from __future__ import annotations

import logging
from typing import Iterable, Optional
import mimetypes

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.urls import reverse
from django.db import transaction

from .models import (
    InquiryAttachment,
    ProjectInquiry,
    ContactMessage,
    Testimonial,
    AboutPage,
    SiteSettings,
)

logger = logging.getLogger(__name__)


# ---------- helpers ----------
def _as_list(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _strip_fields(instance, field_names: Iterable[str]) -> None:
    for fname in field_names:
        val = getattr(instance, fname, None)
        if isinstance(val, str):
            setattr(instance, fname, val.strip())


def _safe_delete_file(fld) -> None:
    try:
        if fld and getattr(fld, "name", "") and getattr(fld, "storage", None) and fld.storage.exists(fld.name):
            fld.storage.delete(fld.name)
    except Exception:
        logger.exception("Failed to delete file %r", getattr(fld, "name", None))


def _abs_url(path: str) -> str:
    """
    Build an absolute URL using SITE_BASE_URL / SITE_URL if provided.
    """
    base = (
        getattr(settings, "SITE_BASE_URL", None)
        or getattr(settings, "SITE_URL", None)
        or ""
    )
    base = (base or "").rstrip("/")
    return f"{base}{path}" if base else path


# ---------- Normalize & defaults ----------
@receiver(pre_save, sender=ContactMessage)
def contactmessage_pre_save(sender, instance: ContactMessage, **kwargs):
    _strip_fields(instance, ["name", "email", "phone", "subject", "message", "user_agent", "referer"])
    if not instance.subject:
        instance.subject = f"Website contact from {instance.name or 'Visitor'}"


@receiver(pre_save, sender=ProjectInquiry)
def projectinquiry_pre_save(sender, instance: ProjectInquiry, **kwargs):
    _strip_fields(
        instance,
        [
            "first_name", "last_name", "email", "alt_email", "company",
            "phone_number", "alt_phone_number",
            "street_address", "city", "state", "zip_code",
            "land_address", "land_city", "land_state", "land_zip_code", "land_size",
            "additional_notes",
        ],
    )


@receiver(pre_save, sender=Testimonial)
def testimonial_pre_save(sender, instance: Testimonial, **kwargs):
    _strip_fields(instance, ["name", "email", "message"])
    # stash old approval so we can detect transition in post_save
    if instance.pk:
        try:
            old = Testimonial.objects.get(pk=instance.pk)
            instance._was_approved = old.approved  # type: ignore[attr-defined]
        except Testimonial.DoesNotExist:
            instance._was_approved = False  # type: ignore[attr-defined]
    else:
        instance._was_approved = False  # type: ignore[attr-defined]


# ---------- Files cleanup ----------
@receiver(post_delete, sender=InquiryAttachment)
def inquiryattachment_post_delete(sender, instance: InquiryAttachment, **kwargs):
    _safe_delete_file(instance.file)


@receiver(post_delete, sender=AboutPage)
def aboutpage_post_delete(sender, instance: AboutPage, **kwargs):
    _safe_delete_file(getattr(instance, "photo_main", None))
    _safe_delete_file(getattr(instance, "photo_secondary", None))


@receiver(post_delete, sender=SiteSettings)
def sitesettings_post_delete(sender, instance: SiteSettings, **kwargs):
    _safe_delete_file(getattr(instance, "brand_logo", None))


# ---------- ProjectInquiry notifications ----------
@receiver(post_save, sender=ProjectInquiry)
def projectinquiry_post_save(sender, instance: ProjectInquiry, created: bool, **kwargs):
    """
    Send an internal notification when a new ProjectInquiry is created.
    Guarded by settings.GET_STARTED_NOTIFY_VIA_SIGNALS (default True).
    """
    if not created:
        return
    if not getattr(settings, "GET_STARTED_NOTIFY_VIA_SIGNALS", True):
        return

    def _send():
        try:
            # recipients (deduped, stripped)
            to_emails = (
                _as_list(getattr(settings, "GET_STARTED_TO_EMAILS", None))
                or _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
            )
            if not to_emails:
                default_from = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
                if default_from:
                    to_emails = [default_from]

            # Build admin URL (best-effort)
            try:
                admin_path = reverse("admin:pages_projectinquiry_change", args=[instance.pk])
            except Exception:
                admin_path = f"/admin/pages/projectinquiry/{instance.pk}/change/"

            ctx = {
                "inquiry": instance,
                "attachments": getattr(instance, "attachments", None).all() if hasattr(instance, "attachments") else [],  # type: ignore
                "admin_url": _abs_url(admin_path),
                "site_url": _abs_url("/"),
            }

            text_body = render_to_string("pages/emails/get_started_notification.txt", ctx)
            try:
                html_body = render_to_string("pages/emails/get_started_notification.html", ctx)
            except Exception:
                html_body = None

            subject_name = f"{(instance.first_name or '').strip()} {(instance.last_name or '').strip()}".strip() or "Visitor"
            subject_email = (instance.email or "").strip()
            subject = f"[Get Started] {subject_name} — {subject_email or 'no-email'}"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                to=[e for e in dict.fromkeys(to_emails) if e],  # dedupe & drop empties
                reply_to=[subject_email] if subject_email else None,
            )
            if html_body:
                msg.attach_alternative(html_body, "text/html")

            # Attach small files directly (<= 5MB each)
            if hasattr(instance, "attachments"):
                for att in instance.attachments.all():  # type: ignore
                    try:
                        f = att.file
                        if not getattr(f, "name", ""):
                            continue
                        size = getattr(f, "size", 0) or 0
                        if size <= 5 * 1024 * 1024:
                            filename = f.name.split("/")[-1]
                            ctype, _ = mimetypes.guess_type(filename)
                            # open/read/close (File.open is not a context manager)
                            f.open("rb")
                            try:
                                data = f.read()
                            finally:
                                f.close()
                            msg.attach(filename, data, ctype or "application/octet-stream")
                    except Exception:
                        logger.exception("Failed attaching file for inquiry #%s", instance.pk)

            # In staging you may set fail_silently=False to surface config issues
            msg.send(fail_silently=True)
        except Exception:
            logger.exception("Failed to send ProjectInquiry notification for #%s", instance.pk)

    # Ensure we only send after the row is committed (avoid duplicates)
    try:
        transaction.on_commit(_send)
    except Exception:
        # If no transaction context, just send
        _send()


# ---------- Testimonial notifications ----------
@receiver(post_save, sender=Testimonial)
def testimonial_post_save(sender, instance: Testimonial, created: bool, **kwargs):
    """
    - On create: notify site owner (optional).
    - On approval transition (approved==True & consent==True): thank the submitter (optional).
    """
    # Admin/owner notify on create
    if created and getattr(settings, "TESTIMONIAL_NOTIFY_ON_CREATE", True):
        try:
            to_emails = (
                _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
                or _as_list(getattr(settings, "GET_STARTED_TO_EMAILS", None))
                or _as_list(getattr(settings, "CRM_NEW_UPLOAD_NOTIFY_TO", None))
            )
            if to_emails:
                ctx = {"t": instance}
                text_body = render_to_string("pages/emails/testimonial_notify.txt", ctx)
                try:
                    html_body = render_to_string("pages/emails/testimonial_notify.html", ctx)
                except Exception:
                    html_body = None

                role_piece = ""
                # include role in subject if the field exists and is non-empty
                role = getattr(instance, "role", "") or ""
                if role:
                    role_piece = f" • {role}"

                msg = EmailMultiAlternatives(
                    subject=f"[Testimonial] New submission: {instance.name}{role_piece}",
                    body=text_body,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=[e for e in dict.fromkeys(to_emails) if e],
                    reply_to=[instance.email] if instance.email else None,
                )
                if html_body:
                    msg.attach_alternative(html_body, "text/html")
                msg.send(fail_silently=True)
        except Exception:
            logger.exception("Failed to send testimonial creation notification")

    # Thank the submitter when published (approved transition)
    was_approved = bool(getattr(instance, "_was_approved", False))
    if (
        not created
        and not was_approved
        and instance.approved
        and instance.consent_to_publish
        and getattr(settings, "TESTIMONIAL_THANK_ON_PUBLISH", True)
        and instance.email
    ):
        try:
            ctx = {"t": instance}
            text_body = render_to_string("pages/emails/testimonial_thanks.txt", ctx)
            try:
                html_body = render_to_string("pages/emails/testimonial_thanks.html", ctx)
            except Exception:
                html_body = None

            ack = EmailMultiAlternatives(
                subject="Thanks — your testimonial is now published",
                body=text_body,
                from_email=getattr(settings, "AUTO_ACK_FROM_EMAIL", getattr(settings, "DEFAULT_FROM_EMAIL", None)),
                to=[instance.email],
            )
            if html_body:
                ack.attach_alternative(html_body, "text/html")
            ack.send(fail_silently=True)
        except Exception:
            logger.exception("Failed to send testimonial publish thank-you")
