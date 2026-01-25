from __future__ import annotations

from decimal import Decimal
from typing import Any

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit

from core.utils import verify_recaptcha_v3, get_client_ip
from .models import HouseStyle as HouseStyleModel, Plans, PlanGallery
from .forms import PlanQuickForm, PlanCommentForm
from .session_utils import get_saved_plan_ids, get_comparison_plan_ids

logger = logging.getLogger(__name__)
# ----- Filter choice lists -----
SQFT_CHOICES = [str(n) for n in range(1000, 6001, 100)]
BED_FILTER_CHOICES = ["1", "2", "3", "4", "5", "6+"]  # interpret 6+ as >=6
BATH_FILTER_CHOICES = ["1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5+"]  # interpret 5+ as >=5


def _as_int(val: str | None) -> int | None:
    try:
        return int(val) if val not in (None, "") else None
    except Exception:
        return None


def _as_decimal(val: str | None) -> Decimal | None:
    try:
        return Decimal(val) if val not in (None, "") else None
    except Exception:
        return None


def _parse_beds(raw: str | None) -> int | None:
    """Return minimum beds as int or None. '6+' -> 6."""
    if not raw:
        return None
    return 6 if raw.strip() == "6+" else _as_int(raw)


def _parse_baths(raw: str | None) -> Decimal | None:
    """Return minimum baths as Decimal or None. '5+' -> 5.0."""
    if not raw:
        return None
    return Decimal("5.0") if raw.strip() == "5+" else _as_decimal(raw)


def _client_ip(request: HttpRequest) -> str:
    """Deprecated: Use get_client_ip from core.utils instead."""
    return get_client_ip(request)


def _ensure_list(value: Any) -> list[str]:
    """Normalize settings values to a list of emails."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _looks_like_gibberish(text: str) -> bool:
    """Lightweight heuristic for obvious bot payloads like vVbHEmdFhhvJENIU."""
    t = (text or "").strip()
    if not t:
        return True

    # If it's a single "word" with no spaces and mostly mixed-case letters, it's often spam
    if " " not in t and len(t) >= 12:
        letters = sum(ch.isalpha() for ch in t)
        if letters / max(len(t), 1) > 0.8:
            upp = sum(ch.isupper() for ch in t)
            low = sum(ch.islower() for ch in t)
            if upp >= 3 and low >= 3:
                return True

    return False


def _verify_recaptcha_v3(request: HttpRequest) -> tuple[bool, float | None]:
    """Deprecated: Use verify_recaptcha_v3 from core.utils instead."""
    return verify_recaptcha_v3(request)


def plan_list(request: HttpRequest, house_style_slug: str | None = None) -> HttpResponse:
    """
    Grid list of plans (3 across, paginated).
    Supports filters & sorting and allows selecting style via route OR ?style=<slug>.
    """
    styles = HouseStyleModel.objects.all().order_by("style_name")
    active_style = None

    qs = Plans.objects.filter(is_available=True).prefetch_related("house_styles")

    # Allow style filter from querystring first; fall back to /style/<slug>/ route.
    style_q = (request.GET.get("style") or "").strip()
    if style_q:
        active_style = get_object_or_404(HouseStyleModel, slug=style_q)
        qs = qs.filter(house_styles=active_style)
    elif house_style_slug:
        active_style = get_object_or_404(HouseStyleModel, slug=house_style_slug)
        qs = qs.filter(house_styles=active_style)

    # Raw query values
    q_raw = (request.GET.get("q") or "").strip()
    min_sqft_raw = request.GET.get("min_sqft") or ""
    max_sqft_raw = request.GET.get("max_sqft") or ""
    beds_raw = request.GET.get("beds") or ""
    baths_raw = request.GET.get("baths") or ""
    sort = request.GET.get("sort", "newest")

    # Apply filters
    if q_raw:
        qs = qs.filter(Q(plan_number__icontains=q_raw) | Q(description__icontains=q_raw))

    min_sqft = _as_int(min_sqft_raw)
    max_sqft = _as_int(max_sqft_raw)
    beds_min = _parse_beds(beds_raw)
    baths_min = _parse_baths(baths_raw)

    if min_sqft is not None:
        qs = qs.filter(square_footage__gte=min_sqft)
    if max_sqft is not None:
        qs = qs.filter(square_footage__lte=max_sqft)
    if beds_min is not None:
        qs = qs.filter(bedrooms__gte=beds_min)
    if baths_min is not None:
        qs = qs.filter(bathrooms__gte=baths_min)

    # Sort
    if sort == "sqft_asc":
        qs = qs.order_by("square_footage", "-created_date")
    elif sort == "sqft_desc":
        qs = qs.order_by("-square_footage", "-created_date")
    elif sort == "price_asc":
        qs = qs.order_by("plan_price", "-created_date")
    elif sort == "price_desc":
        qs = qs.order_by("-plan_price", "-created_date")
    else:
        qs = qs.order_by("-created_date")

    paginator = Paginator(qs, 12)  # 12 per page (3 across x 4 rows)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    ctx = {
        "page": {"title": "Plans", "description": "Explore our house plans."},
        "plans": page_obj,
        "plan_count": qs.count(),
        "styles": styles,
        "active_style": active_style,
        "sqft_choices": SQFT_CHOICES,
        "bed_choices": BED_FILTER_CHOICES,
        "bath_choices": BATH_FILTER_CHOICES,
        "filters": {
            "style": active_style.slug if active_style else style_q,
            "q": q_raw,
            "min_sqft": min_sqft_raw,
            "max_sqft": max_sqft_raw,
            "beds": beds_raw,
            "baths": baths_raw,
            "sort": sort,
        },
        "saved_plan_ids": get_saved_plan_ids(request),
        "comparison_plan_ids": get_comparison_plan_ids(request),
    }
    return render(request, "plans/plans.html", ctx)


def plan_detail(request: HttpRequest, house_style_slug: str, plan_slug: str) -> HttpResponse:
    """
    Single plan detail + gallery + request changes form (no user deps).
    """
    plan = get_object_or_404(
        Plans.objects.prefetch_related("house_styles"),
        house_styles__slug=house_style_slug,
        slug=plan_slug,
    )

    # Track this plan as recently viewed
    from . import session_utils
    session_utils.track_viewed_plan(request, plan.id)

    images = list(PlanGallery.objects.filter(plan=plan).order_by("order", "id"))
    base_price: Decimal = plan.plan_price or Decimal("0")

    ctx = {
        "plan": plan,
        "images": images,
        "base_price": base_price,
        "page": {"title": f"Plan {plan.plan_number}"},
        "comment_form": PlanCommentForm(),
        "recaptcha_site_key": (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip(),
        "is_saved": session_utils.is_plan_saved(request, plan.id),
        "is_in_comparison": session_utils.is_in_comparison(request, plan.id),
    }
    return render(request, "plans/plan_detail.html", ctx)


def search(request: HttpRequest) -> HttpResponse:
    """Simple search endpoint; reuses list template with a keyword filter."""
    q_raw = (request.GET.get("q") or "").strip()
    qs = Plans.objects.filter(is_available=True).prefetch_related("house_styles")
    if q_raw:
        qs = qs.filter(Q(plan_number__icontains=q_raw) | Q(description__icontains=q_raw))

    paginator = Paginator(qs.order_by("-created_date"), 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    ctx = {
        "page": {"title": "Plans", "description": f"Search results for '{q_raw}'"},
        "plans": page_obj,
        "plan_count": qs.count(),
        "styles": HouseStyleModel.objects.all().order_by("style_name"),
        "active_style": None,
        "sqft_choices": SQFT_CHOICES,
        "bed_choices": BED_FILTER_CHOICES,
        "bath_choices": BATH_FILTER_CHOICES,
        "filters": {"q": q_raw, "sort": "newest", "min_sqft": "", "max_sqft": "", "beds": "", "baths": "", "style": ""},
    }
    return render(request, "plans/plans.html", ctx)


# -------- Staff-only: quick create a plan from the list page --------
@staff_member_required
@require_POST
def quick_create_plan(request: HttpRequest) -> HttpResponse:
    form = PlanQuickForm(request.POST, request.FILES)
    if not form.is_valid():
        for field, errs in list(form.errors.items())[:6]:
            messages.error(request, f"{field}: {', '.join(errs)}")  # type: ignore
        return redirect("plans:plan_list")

    plan: Plans = form.save(commit=False)

    if not getattr(plan, "slug", None):
        from django.utils.text import slugify
        plan.slug = slugify(str(plan.plan_number))

    if "is_available" not in form.cleaned_data:
        plan.is_available = True

    plan.save()
    form.save_m2m()

    files = form.cleaned_data.get("gallery_images") or []
    first_pg = None
    for f in files:
        pg = PlanGallery.objects.create(plan=plan, image=f)
        if first_pg is None:
            first_pg = pg

    if not plan.main_image and first_pg:
        plan.main_image = first_pg.image  # type: ignore
        plan.save(update_fields=["main_image"])

    messages.success(request, f"Plan {plan.plan_number} created.")
    return redirect(plan.get_absolute_url())


# -------- Staff-only gallery actions --------
@staff_member_required
@require_POST
def gallery_upload(request: HttpRequest, plan_id: int) -> HttpResponse:
    plan = get_object_or_404(Plans, pk=plan_id)
    files = request.FILES.getlist("images")
    if not files:
        messages.error(request, "No files selected.")
        return redirect(plan.get_absolute_url())

    for f in files:
        PlanGallery.objects.create(plan=plan, image=f)

    messages.success(request, f"Uploaded {len(files)} image(s).")
    return redirect(plan.get_absolute_url())


@staff_member_required
@require_POST
def gallery_delete(request: HttpRequest, image_id: int) -> HttpResponse:
    img = get_object_or_404(PlanGallery, pk=image_id)
    plan_url = img.plan.get_absolute_url()
    img.delete()
    messages.success(request, "Image deleted.")
    return redirect(plan_url)


@staff_member_required
@require_POST
def gallery_make_cover(request: HttpRequest, image_id: int) -> HttpResponse:
    img = get_object_or_404(PlanGallery, pk=image_id)
    plan = img.plan
    plan.main_image = img.image  # type: ignore
    plan.save(update_fields=["main_image"])
    messages.success(request, "Cover image updated.")
    return redirect(plan.get_absolute_url())


@require_POST
def send_plan_comment(request: HttpRequest, plan_id: int) -> HttpResponse:
    """
    Public: send 'change request' email.

    Protections:
    - Rate limit by IP
    - Honeypot field
    - reCAPTCHA v3 score verification
    - Gibberish / low-quality gate
    - Silent discard on suspected spam (show generic success)
    """
    try:
        plan = get_object_or_404(Plans, pk=plan_id)

        # If rate limited, tell the user instead of silently succeeding
        if getattr(request, "limited", False):
            messages.error(request, "You've sent a few requests recently. Please wait a bit and try again.")
            return redirect(plan.get_absolute_url())

        form = PlanCommentForm(request.POST)

        # If form invalid - check if it's spam or real validation error
        if not form.is_valid():
            # If honeypot was filled or terms not checked, treat as spam (silent success)
            if form.data.get("website") or not form.data.get("terms"):
                messages.success(request, "Thanks! Your request has been emailed. We'll follow up soon.")
                return redirect(plan.get_absolute_url())
            
            # Otherwise show actual validation errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
            return redirect(plan.get_absolute_url())

        name = (form.cleaned_data.get("name") or "").strip()
        email = (form.cleaned_data.get("email") or "").strip()
        message = (form.cleaned_data.get("message") or "").strip()

        # Require a reasonable message length and reject obvious gibberish with a clear error
        min_len = int(getattr(settings, "PLAN_CHANGE_MIN_MESSAGE_LEN", 10))
        if len(message) < min_len:
            messages.error(request, f"Please provide a bit more detail (at least {min_len} characters).")
            return redirect(plan.get_absolute_url())

        if _looks_like_gibberish(message):
            messages.error(request, "That message looks like spam. Please rephrase and try again.")
            return redirect(plan.get_absolute_url())

        # reCAPTCHA v3 verification (enforced if secret is configured)
        recaptcha_ok, score = _verify_recaptcha_v3(request)
        if not recaptcha_ok:
            messages.error(request, "Spam detection failed. Please try again.")
            return redirect(plan.get_absolute_url())

        subject = f"[Plan {plan.plan_number}] Change request"
        lines = [
            f"Plan: {plan.plan_number}",
            f"URL: {request.build_absolute_uri(plan.get_absolute_url())}",
            f"IP: {_client_ip(request)}",
        ]
        if score is not None:
            lines.append(f"reCAPTCHA score: {score:.2f}")
        if name:
            lines.append(f"From: {name}")
        if email:
            lines.append(f"Email: {email}")
        lines.extend(("", "Message:", message))
        body = "\n".join(lines)

        to_emails = _ensure_list(getattr(settings, "CONTACT_TO_EMAILS", None)) or _ensure_list(getattr(settings, "DEFAULT_FROM_EMAIL", None))
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or (to_emails[0] if to_emails else None)

        if not to_emails:
            messages.error(request, "Email configuration is missing. Please contact us directly.")
            return redirect(plan.get_absolute_url())

        try:
            EmailMessage(
                subject=subject,
                body=body,
                from_email=from_email,
                to=to_emails,
                reply_to=[email] if email else None,
            ).send(fail_silently=False)
            logger.info(f"Plan change request email sent: Plan {plan.plan_number}, From: {email or 'anonymous'}, To: {to_emails}")

        except Exception as e:
            logger.error(f"Failed to send plan change request email: {e}", exc_info=True)
            messages.error(request, "Sorry, there was an error sending your message. Please try again or contact us directly.")
            return redirect(plan.get_absolute_url())
        
        # Send auto-ack to submitter if they provided email
        if email:
            try:
                ack_text = (
                    f"Hi{' ' + name if name else ''},\n\n"
                    f"Thanks for your interest in plan {plan.plan_number}! "
                    "We received your change request and will get back to you shortly with details.\n\n"
                    f"Your request: {message[:200]}{'...' if len(message) > 200 else ''}\n\n"
                    "We'll be in touch soon.\n\n"
                    "— Provost Home Design"
                )
                from django.core.mail import EmailMultiAlternatives
                ack = EmailMultiAlternatives(
                    subject=f"Thanks for your interest in plan {plan.plan_number}",
                    body=ack_text,
                    from_email=(
                        getattr(settings, "AUTO_ACK_FROM_EMAIL", None)
                        or getattr(settings, "DEFAULT_FROM_EMAIL", None)
                        or (to_emails[0] if to_emails else None)
                    ),
                    to=[email],
                )
                ack.send(fail_silently=True)
            except Exception:
                pass  # Silent fail for ack

        messages.success(request, "Thanks! Your request has been emailed. We'll follow up soon.")
        return redirect(plan.get_absolute_url())

    except Exception:
        safe_url = "/"  # default fallback
        try:
            plan = Plans.objects.filter(pk=plan_id).first()
            if plan:
                safe_url = plan.get_absolute_url()
        except Exception:
            pass
        try:
            messages.error(request, "We hit a server error. Please try again or contact us directly.")
        except Exception:
            pass
        return redirect(safe_url)


# -----------------------------
# Favorites/Wishlist Views
# -----------------------------
from . import session_utils
from django.http import JsonResponse


@require_POST
def toggle_favorite(request: HttpRequest, plan_id: int) -> HttpResponse:
    """Add or remove a plan from favorites."""
    session_utils.ensure_session_key(request)
    plan = get_object_or_404(Plans, pk=plan_id, is_available=True)
    
    is_saved = session_utils.is_plan_saved(request, plan_id)
    
    if is_saved:
        session_utils.remove_from_saved_plans(request, plan_id)
        action = "removed"
        message = f"Plan {plan.plan_number} removed from favorites"
    else:
        session_utils.add_to_saved_plans(request, plan_id)
        action = "added"
        message = f"Plan {plan.plan_number} added to favorites"
    
    # Always return JSON for POST requests (AJAX)
    return JsonResponse({
        "success": True,
        "action": action,
        "is_saved": not is_saved,
        "count": len(session_utils.get_saved_plan_ids(request)),
    })


def favorites_list(request: HttpRequest) -> HttpResponse:
    """View all saved/favorite plans."""
    saved_ids = session_utils.get_saved_plan_ids(request)
    
    if saved_ids:
        # Preserve order from session
        plans = Plans.objects.filter(id__in=saved_ids, is_available=True).prefetch_related("house_styles")
        # Sort by session order
        plans_dict = {p.id: p for p in plans}
        plans_ordered = [plans_dict[pid] for pid in saved_ids if pid in plans_dict]
    else:
        plans_ordered = []
    
    context = {
        "page": {"title": "My Favorites", "description": "Your saved house plans"},
        "saved_plans": plans_ordered,  # Template expects 'saved_plans', not 'plans'
        "saved_count": len(saved_ids),
        "comparison_plan_ids": session_utils.get_comparison_plan_ids(request),
    }
    return render(request, "plans/favorites.html", context)


# -----------------------------
# Comparison Views
# -----------------------------

@require_POST
def toggle_comparison(request: HttpRequest, plan_id: int) -> HttpResponse:
    """Add or remove a plan from comparison list."""
    session_utils.ensure_session_key(request)
    plan = get_object_or_404(Plans, pk=plan_id, is_available=True)
    
    is_in_comp = session_utils.is_in_comparison(request, plan_id)
    
    if is_in_comp:
        session_utils.remove_from_comparison(request, plan_id)
        action = "removed"
        message = f"Plan {plan.plan_number} removed from comparison"
        success = True
    else:
        success, error = session_utils.add_to_comparison(request, plan_id)
        if success:
            action = "added"
            message = f"Plan {plan.plan_number} added to comparison"
        else:
            action = "error"
            message = error or "Could not add plan to comparison"
    
    # Always return JSON for POST requests (AJAX)
    return JsonResponse({
        "success": success,
        "action": action,
        "in_comparison": not is_in_comp if success else is_in_comp,
        "count": len(session_utils.get_comparison_plan_ids(request)),
        "message": message,
        "error": None if success else message,
    })


def compare_plans(request: HttpRequest) -> HttpResponse:
    """Compare multiple plans side-by-side."""
    comparison_ids = session_utils.get_comparison_plan_ids(request)
    
    if comparison_ids:
        plans = Plans.objects.filter(
            id__in=comparison_ids,
            is_available=True
        ).prefetch_related("house_styles", "images")
        
        # Sort by session order
        plans_dict = {p.id: p for p in plans}
        plans_ordered = [plans_dict[pid] for pid in comparison_ids if pid in plans_dict]
    else:
        plans_ordered = []
    
    context = {
        "page": {"title": "Compare Plans", "description": "Side-by-side plan comparison"},
        "plans": plans_ordered,
        "comparison_count": len(comparison_ids),
    }
    return render(request, "plans/compare.html", context)


@require_POST
def clear_comparison_view(request: HttpRequest) -> HttpResponse:
    """Clear all plans from comparison."""
    session_utils.clear_comparison(request)
    messages.success(request, "Comparison cleared")
    return redirect("plans:plan_list")
