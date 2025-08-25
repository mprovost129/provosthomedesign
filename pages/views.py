# pages/views.py
from __future__ import annotations
from time import time
from django.core.exceptions import ValidationError
from django_ratelimit.decorators import ratelimit

import contextlib
import logging
import mimetypes
import re
from typing import Iterable

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from .forms import ContactForm, NewHouseForm, TestimonialForm
from .models import (
    InquiryAttachment,
    ProjectInquiry,
    Testimonial,
    AboutPage,
    SiteSettings,
)
from plans.models import Plans, HouseStyle
from django.core.paginator import Paginator
from pages.models import Testimonial

logger = logging.getLogger(__name__)

# ----- Helpers ---------------------------------------------------------------

def _as_list(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\n;]+", value.strip())
        return [p.strip() for p in parts if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]

def _normalize_phone(phone: str) -> tuple[str, str]:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return "", ""
    if len(digits) == 10:
        e164 = f"+1{digits}"
    elif digits.startswith("1") and len(digits) == 11:
        e164 = f"+{digits}"
    else:
        e164 = f"+{digits}"
    return e164, f"tel:{e164}"

def _parse_address(address: str) -> tuple[str, str, str, str]:
    street, locality, region, postal = address, "", "", ""
    if address:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 3:
            street = parts[0]
            locality = parts[1]
            tail = parts[2].split()
            if len(tail) >= 2:
                region, postal = tail[0], tail[1]
    return street, locality, region, postal

def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"

def _htmx_status(request: HttpRequest, level: str, message: str) -> HttpResponse:
    return render(request, "pages/partials/contact_status.html", {"level": level, "text": message})

THROTTLE_WINDOW_SECONDS = 60

def _too_many_recent_submissions(request: HttpRequest) -> bool:
    last = request.session.get("last_contact_submission_ts")
    now = timezone.now().timestamp()
    if last and (now - last) < THROTTLE_WINDOW_SECONDS:
        return True
    request.session["last_contact_submission_ts"] = now
    return False

# ----- Views ----------------------------------------------------------------

def home(request: HttpRequest) -> HttpResponse:
    recent_plans = (
        Plans.objects
        .filter(is_available=True)
        .select_related("house_style")
        .only(
            "id", "slug", "plan_number", "plan_price", "square_footage",
            "bedrooms", "bathrooms", "main_image", "created_date",
            "house_style__style_name", "house_style__slug"
        )
        .order_by("-created_date")[:3]
    )

    recent_testimonials = list(
        Testimonial.objects
        .filter(approved=True, consent_to_publish=True)
        .only("id", "name", "rating", "message", "created_at")
        .order_by("-created_at")[:3]
    )

    # NEW: expose a few styles for the chips
    house_styles = HouseStyle.objects.only("slug", "style_name").order_by("style_name")[:8]

    return render(
        request,
        "pages/home.html",
        {
            "recent_plans": recent_plans,
            "recent_testimonials": recent_testimonials,
            "house_styles": house_styles,  # <- add this
        },
    )

@ratelimit(key="ip", rate="5/m", block=True)
def contact(request: HttpRequest) -> HttpResponse:
    # Brand/contact settings
    s = SiteSettings.load()
    company = s.company_name or getattr(settings, "COMPANY_NAME", "Provost Home Design")
    owner = s.contact_name or getattr(settings, "CONTACT_NAME", "Michael Provost")
    phone = s.contact_phone or getattr(settings, "CONTACT_PHONE", "508-243-7912")
    address = s.contact_address or getattr(settings, "CONTACT_ADDRESS", "7 Park St. Unit 1, Rehoboth, MA 02769")
    email = s.contact_email or getattr(settings, "CONTACT_EMAIL", "mike@provosthomedesign.com")
    logo_url = s.logo_url

    e164, tel_href = _normalize_phone(phone)
    mailto_href = f"mailto:{email}" if email else ""
    street, locality, region, postal = _parse_address(address)

    # Structured business hours (ordered by Meta.ordering)
    hours_struct = list(getattr(s, "hours").all()) if hasattr(s, "hours") else []

    # Build a display-ready list for the template
    DAY_LABELS = dict([("sun","Sunday"),("mon","Monday"),("tue","Tuesday"),
                       ("wed","Wednesday"),("thu","Thursday"),("fri","Friday"),("sat","Saturday")])

    def _fmt(t):
        return t.strftime("%I:%M %p").lstrip("0") if t else ""

    hours_display: list[dict[str, str]] = []
    for h in hours_struct:
        if getattr(h, "is_closed", False):
            span = "Closed"
        elif getattr(h, "by_appointment", False):
            span = "By Appointment"
        elif getattr(h, "open_time", None) and getattr(h, "close_time", None):
            span = f"{_fmt(h.open_time)} – {_fmt(h.close_time)}"
        else:
            span = "—"
        hours_display.append({"day": DAY_LABELS.get(h.day, h.day), "span": span})  # type: ignore

    # Email recipients
    to_emails = (
        _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
        or [email or getattr(settings, "DEFAULT_FROM_EMAIL", "")]
    )
    bcc_emails = _as_list(getattr(settings, "CONTACT_BCC_EMAILS", None))

    # Forms
    contact_form = ContactForm(request.POST or None, prefix="contact")
    tform = TestimonialForm(request.POST or None, prefix="testimonial")

    action = request.POST.get("action", "").strip().lower() if request.method == "POST" else ""
    is_contact_post = action == "send_message" or any(k.startswith("contact-") for k in request.POST.keys())
    is_testimonial_post = action == "submit_testimonial" or any(k.startswith("testimonial-") for k in request.POST.keys())

    # Seed the timing token on GET
    if request.method != "POST":
        request.session["contact_started_ts"] = time()

    if request.method == "POST":
        # 1) Basic per-session throttle
        if _too_many_recent_submissions(request):
            msg = "You're sending messages too quickly. Please wait a minute and try again."
            if _is_htmx(request):
                return _htmx_status(request, "warning", msg)
            messages.warning(request, msg)
            return redirect("pages:contact")

        # 2) “Too fast” submission (likely bot)
        started = float(request.session.get("contact_started_ts", 0))
        if time() - started < 2.0:
            # reset seed for the next legit attempt
            request.session["contact_started_ts"] = time()
            logger.info(
                "Spam gate tripped: too_fast ip=%s ua=%s",
                request.META.get("REMOTE_ADDR"),
                request.META.get("HTTP_USER_AGENT"),
            )
            if _is_htmx(request):
                return _htmx_status(request, "error", "Spam protection triggered. Please try again.")
            messages.error(request, "Spam protection triggered. Please try again.")
            return redirect("pages:contact")

        # refresh seed to avoid reusing the same timestamp
        request.session["contact_started_ts"] = time()

        # Contact submission
        if is_contact_post:
            if not request.POST.get(f"{contact_form.prefix}-terms_accepted") and hasattr(contact_form, "fields") and "terms_accepted" in contact_form.fields:
                contact_form.add_error("terms_accepted", "You must accept the Terms & Conditions.")

            if contact_form.is_valid():
                cd = contact_form.cleaned_data
                sub = cd.get("subject") or f"Contact request from {cd['name']}"
                message_id_display = "-"

                ctx = {
                    "company": company,
                    "name": owner,
                    "logo_url": logo_url,
                    "from_name": cd["name"],
                    "from_email": cd["email"],
                    "from_phone": cd.get("phone", ""),
                    "subject_line": sub,
                    "message": cd["message"],
                    "request_url": request.build_absolute_uri(),
                    "message_id": message_id_display,
                    "terms_accepted": bool(cd.get("terms_accepted")),
                }

                try:
                    text_body = render_to_string("pages/emails/contact_notification.txt", ctx)
                    html_body = render_to_string("pages/emails/contact_notification.html", ctx)
                except Exception:
                    logger.exception("Missing/broken contact notification templates")
                    if _is_htmx(request):
                        return _htmx_status(request, "error", "We had a problem preparing the email template.")
                    messages.error(request, "We had a problem preparing the email template.")
                    return redirect("pages:contact")

                msg = EmailMultiAlternatives(
                    subject=f"{getattr(settings, 'CONTACT_EMAIL_SUBJECT_PREFIX', '[Contact]')} {sub}",
                    body=text_body,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=to_emails or [getattr(settings, "DEFAULT_FROM_EMAIL", "")],
                    bcc=bcc_emails or None,
                    reply_to=[cd["email"]],
                )
                msg.attach_alternative(html_body, "text/html")
                try:
                    msg.send(fail_silently=False)
                except Exception as e:
                    logger.exception("Contact email send failed")
                    err = "We couldn't send your message just now. Please try again in a moment."
                    if settings.DEBUG:
                        err += f" ({e.__class__.__name__}: {e})"
                    if _is_htmx(request):
                        return _htmx_status(request, "error", err)
                    messages.error(request, err)
                    return redirect("pages:contact")

                # Auto-ack
                with contextlib.suppress(Exception):
                    ack_ctx = {
                        "name": cd["name"],
                        "subject": sub,
                        "message": cd["message"],
                        "message_id": message_id_display,
                        "company": company,
                        "logo_url": logo_url,
                    }
                    try:
                        ack_html = render_to_string("pages/emails/contact_ack.html", ack_ctx)
                    except Exception:
                        ack_html = None

                    ack_text = (
                        f"Hi {cd['name']},\n\n"
                        "Thanks for reaching out. We received your message and will reply soon.\n\n"
                        f"Subject: {sub}\n"
                        f"Message ID: {message_id_display}\n\n"
                        "— The Team"
                    )
                    ack = EmailMultiAlternatives(
                        subject="Thanks — we received your message",
                        body=ack_text,
                        from_email=getattr(settings, "AUTO_ACK_FROM_EMAIL", getattr(settings, "DEFAULT_FROM_EMAIL", None)),
                        to=[cd["email"]],
                    )
                    if ack_html:
                        ack.attach_alternative(ack_html, "text/html")
                    ack.send(fail_silently=True)

                success_msg = "Thanks! Your message has been sent. We'll get back to you soon."
                if _is_htmx(request):
                    resp = _htmx_status(request, "success", success_msg)
                    resp["X-Contact-Success"] = "1"
                    return resp
                messages.success(request, success_msg)
                return redirect("pages:contact")

            # invalid
            if _is_htmx(request):
                return _htmx_status(request, "error", "Please correct the errors below and resubmit.")
            if "terms_accepted" in contact_form.errors:
                messages.error(request, "Please accept the Terms & Conditions to continue.")
            else:
                messages.error(request, "Please correct the errors below.")

        # Testimonial submission
        if is_testimonial_post:
            if not request.POST.get(f"{tform.prefix}-terms_accepted") and hasattr(tform, "fields") and "terms_accepted" in tform.fields:
                tform.add_error("terms_accepted", "You must accept the Terms & Conditions.")
            if tform.is_valid():
                cd = tform.cleaned_data
                t = Testimonial.objects.create(
                    name=cd["name"],
                    email=cd.get("email", ""),
                    rating=int(cd["rating"]),
                    message=cd["message"],
                    consent_to_publish=cd["consent_to_publish"],
                    approved=False,
                )

                with contextlib.suppress(Exception):
                    admin_url = request.build_absolute_uri(reverse("admin:pages_testimonial_change", args=[t.pk]))
                    site_name = getattr(settings, "SITE_NAME", company)
                    ctx = {"t": t, "admin_url": admin_url, "site_name": site_name}

                    to_admin = (
                        _as_list(getattr(settings, "TESTIMONIAL_TO_EMAILS", None))
                        or _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
                        or [getattr(settings, "DEFAULT_FROM_EMAIL", "")]
                    )

                    subj = f"New testimonial submitted: {t.name or '(No name)'} ({t.rating}/5)"
                    text_body = render_to_string("pages/emails/testimonial_new.txt", ctx)
                    html_body = render_to_string("pages/emails/testimonial_new.html", ctx)

                    em = EmailMultiAlternatives(
                        subject=subj,
                        body=text_body,
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                        to=to_admin,
                        reply_to=[t.email] if t.email else None,
                    )
                    em.attach_alternative(html_body, "text/html")
                    try:
                        em.send(fail_silently=False)
                    except Exception:
                        logger.exception("Testimonial email send failed")

                if _is_htmx(request):
                    return _htmx_status(request, "success", "Thanks! Your testimonial was submitted and will appear once approved.")
                messages.success(request, "Thanks! Your testimonial was submitted and will appear once approved.")
                return redirect("pages:contact")

            if _is_htmx(request):
                return _htmx_status(request, "error", "Please correct the errors in the testimonial form.")
            if "terms_accepted" in tform.errors:
                messages.error(request, "Please accept the Terms & Conditions to submit your testimonial.")
            else:
                messages.error(request, "Please correct the errors in the testimonial form.")

    approved_testimonials = Testimonial.objects.filter(approved=True, consent_to_publish=True)[:6]

    context = {
        "page": {"title": "Contact", "description": f"Get in touch with {company}."},
        "contact": {
            "company": company,
            "name": owner,
            "phone": phone,
            "tel_href": tel_href,
            "email": email,
            "mailto_href": mailto_href,
            "address": address,
            "logo_url": logo_url,
        },
        "schema": {
            "name": company,
            "street": street,
            "locality": locality,
            "region": region,
            "postal": postal,
            "telephone": e164 or phone,
            "email": email,
        },
        "hours_struct": hours_struct,
        "hours_display": hours_display,
        "hours": s.business_hours or getattr(settings, "BUSINESS_HOURS", None),
        "form": contact_form,
        "tform": tform,
        "approved_testimonials": approved_testimonials,
    }
    return render(request, "pages/contact.html", context)

def get_started(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = NewHouseForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data

            def to_int(v):
                try:
                    return int(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            hs_val = cd.get("house_style")
            hs_obj: HouseStyle | None = None
            if isinstance(hs_val, HouseStyle):
                hs_obj = hs_val
            elif isinstance(hs_val, str) and hs_val:
                hs_obj = HouseStyle.objects.filter(slug=hs_val).first()

            inquiry = ProjectInquiry.objects.create(
                first_name=cd["first_name"],
                last_name=cd["last_name"],
                email=cd["email"],
                alt_email=cd.get("alt_email") or "",
                company=cd.get("company") or "",
                phone_number=cd["phone_number"],
                alt_phone_number=cd.get("alt_phone_number") or "",
                preferred_contact_method=cd.get("preferred_contact_method") or "email",
                street_address=cd.get("street_address") or "",
                city=cd.get("city") or "",
                state=cd.get("state") or "",
                zip_code=cd.get("zip_code") or "",
                house_style=hs_obj,
                min_square_footage=to_int(cd.get("min_square_footage")),
                max_square_footage=to_int(cd.get("max_square_footage")),
                budget=cd.get("budget"),
                number_of_floors=to_int(cd.get("number_of_floors")),
                number_of_bedrooms=to_int(cd.get("number_of_bedrooms")),
                number_of_bathrooms=to_int(cd.get("number_of_bathrooms")),
                number_of_garage_spaces=to_int(cd.get("number_of_garage_spaces")),
                land_purchased=bool(cd.get("land_purchased")),
                land_address=cd.get("land_address") or "",
                land_city=cd.get("land_city") or "",
                land_state=cd.get("land_state") or "",
                land_zip_code=cd.get("land_zip_code") or "",
                land_size=cd.get("land_size") or "",
                pre_existing_plans=bool(cd.get("pre_existing_plans")),
                foundation_height=cd.get("foundation_height") or "",
                first_floor_height=cd.get("first_floor_height") or "",
                second_floor_height=cd.get("second_floor_height") or "",
                third_floor_height=cd.get("third_floor_height") or "",
                ceiling_feature_1=cd.get("ceiling_feature_1") or "",
                ceiling_feature_2=cd.get("ceiling_feature_2") or "",
                ceiling_feature_3=cd.get("ceiling_feature_3") or "",
                additional_notes=cd.get("additional_notes") or "",
                terms_accepted=bool(cd.get("terms_accepted")),
            )

            attachments = []
            for f in request.FILES.getlist("plan_files"):
                att = InquiryAttachment.objects.create(inquiry=inquiry, file=f)
                attachments.append(att)

            to_emails = getattr(settings, "GET_STARTED_TO_EMAILS", None) or [getattr(settings, "DEFAULT_FROM_EMAIL", "")]
            to_emails = [e for e in to_emails if e]

            ctx = {
                "inquiry": inquiry,
                "attachments": inquiry.attachments.all(),  # type: ignore
                "admin_url": request.build_absolute_uri(f"/admin/pages/projectinquiry/{inquiry.pk}/change/"),
                "site_url": request.build_absolute_uri("/"),
            }
            subject = f"[Get Started] {inquiry.first_name} {inquiry.last_name} — {inquiry.email}"
            text_body = render_to_string("pages/emails/get_started_notification.txt", ctx)
            html_body = render_to_string("pages/emails/get_started_notification.html", ctx)

            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                to=to_emails or None,
                reply_to=[inquiry.email],
            )
            msg.attach_alternative(html_body, "text/html")

            for att in attachments:
                with contextlib.suppress(Exception):
                    if att.file and att.file.size <= 5 * 1024 * 1024:
                        att.file.open("rb")
                        data = att.file.read()
                        att.file.close()
                        filename = att.file.name.split("/")[-1]
                        ctype, _ = mimetypes.guess_type(filename)
                        msg.attach(filename, data, ctype or "application/octet-stream")

            msg.send(fail_silently=True)

            messages.success(request, "Thanks! Your request was received. We’ll reach out soon.")
            return redirect("pages:get_started")

        messages.error(request, "Please fix the errors below.")
    else:
        form = NewHouseForm()

    return render(
        request,
        "pages/get_started.html",
        {"page": {"title": "Get Started", "description": "Tell us about your project."}, "form": form},
    )

def terms(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/terms.html",
        {"page": {"title": "Terms & Conditions", "description": "Please review our terms."}},
    )

def about(request: HttpRequest) -> HttpResponse:
    s = SiteSettings.load()
    ap = AboutPage.objects.filter(is_published=True).first()

    photos = []
    if ap and ap.photo_main:
        with contextlib.suppress(Exception):
            photos.append(ap.photo_main.url)
    if ap and ap.photo_secondary:
        with contextlib.suppress(Exception):
            photos.append(ap.photo_secondary.url)
    if not photos:
        photos = [static("images/Michael_1.jpg"), static("images/michael_2.jpg")]

    about_ctx = {
        "title": ap.title if ap else "About",
        "company": s.company_name or getattr(settings, "COMPANY_NAME", "Provost Home Design"),
        "owner_name": ap.owner_name if ap and ap.owner_name else (s.contact_name or "Michael Provost"),
        "subtitle": ap.subtitle if ap else "Owner & Principal Designer",
        "paragraphs": ap.paragraphs() if ap else [
            "Michael grew up in the construction business. His father has owned and run a successful construction company since 1989. During Summer breaks, Michael would shadow his father going from site to site, helping any way he could. This is where it became apparent that he had a knack for construction and design!",
            "Michael graduated from the New England Institute of Technology with a degree in Architectural Building & Engineering Technology. In 2006 he was given the opportunity to work at National Lumber as an Engineered Wood Products Designer. This is where he perfected the knowledge of structural design that was taught to him in college. In 2012 he was promoted to Senior EWP Designer. Starting in 2013, Michael was given the opportunity to learn floor & roof truss design.",
            "In 2018 Michael took it upon himself to design a home for his family. Using all of his construction, academics, and design experience, his future career path was born. In February of 2020, Michael established Provost Home Design. Then in October of 2020, Michael left National Lumber to focus full time on his business.",
        ],
        "highlights": ap.highlights_list() if ap else ["Residential design", "Plan revisions", "Permit sets", "Builder coordination"],
        "badges": ap.badges_list() if ap else [],
        "knowledge_skills": ap.knowledge_skills_list() if ap else [
            "Schematic design", "Construction documents", "Residential code literacy",
            "Site planning", "Framing details", "MEP coordination (residential)",
            "Energy compliance basics", "Client communication",
        ],
        "licenses": ap.licenses_list() if ap else [
            "Construction Supervisor – Unrestricted – License # CS-097686",
            "Real Estate Salesperson - Heritage Realty - License # 9581505",
        ],
        "photos": photos,
    }

    testimonials = list(
        Testimonial.objects
        .filter(approved=True, consent_to_publish=True)
        .only("id", "name", "rating", "message", "created_at")
        .order_by("-created_at")[:6]
    )

    return render(request, "pages/about.html", {"about": about_ctx, "testimonials": testimonials})

def privacy(request: HttpRequest) -> HttpResponse:
    s = SiteSettings.load()
    company = s.company_name or getattr(settings, "COMPANY_NAME", "Provost Home Design")
    contact_email = s.contact_email or getattr(settings, "CONTACT_EMAIL", "mike@provosthomedesign.com")
    return render(
        request,
        "pages/privacy.html",
        {
            "page": {"title": "Privacy Policy", "description": "How we handle your data."},
            "company": company,
            "contact_email": contact_email,
        },
    )

def testimonials_list(request):
    qs = Testimonial.objects.filter(
        approved=True, consent_to_publish=True
    ).order_by("-created_at")
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(request, "pages/testimonials_list.html", {"page": page})

from django.shortcuts import render

def services(request):
    return render(request, "pages/services.html")

def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")