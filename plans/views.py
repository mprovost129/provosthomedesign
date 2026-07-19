from __future__ import annotations

from decimal import Decimal
from typing import Any

import json
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
STORY_FILTER_CHOICES = ["1", "2", "3+"]
GARAGE_FILTER_CHOICES = ["0", "1", "2", "3+"]
FEATURE_FILTERS = {
    "adu": ("is_adu", "ADU / carriage house"),
    "first-floor-primary": ("first_floor_primary", "First-floor primary suite"),
    "office": ("has_home_office", "Home office"),
    "pantry": ("has_walk_in_pantry", "Walk-in pantry"),
    "mudroom": ("has_mudroom", "Mudroom"),
    "porch-deck": ("has_porch_or_deck", "Porch or deck"),
    "bonus-room": ("has_bonus_room", "Bonus room"),
    "basement": ("basement_compatible", "Basement compatible"),
    "narrow-lot": ("narrow_lot", "Narrow lot"),
    "multigenerational": ("multigenerational", "Multigenerational"),
}

CATEGORY_PAGES = {
    "ranch-house-plans": {"title": "Ranch House Plans", "meta": "Browse one-story ranch house plans designed for comfortable New England living.", "intro": "Ranch homes bring everyday living onto one accessible level. These plans emphasize efficient circulation, practical room relationships, and straightforward construction while leaving room for porches, attached garages, and varied exterior character.", "style": "ranch", "faqs": [("Why choose a ranch plan?", "Single-level living can simplify circulation, accessibility, and long-term use."), ("Can a ranch plan include a basement?", "Many can, depending on the plan, site, foundation design, and local requirements.")]},
    "colonial-house-plans": {"title": "Colonial House Plans", "meta": "Explore Colonial house plans with efficient two-story layouts and enduring New England character.", "intro": "Colonial plans use a compact two-story form to create efficient footprints and a familiar New England presence. The style adapts well to formal or open interiors, attached garages, rear additions, and traditional exterior detailing.", "style": "colonial", "faqs": [("Are Colonial plans always symmetrical?", "Traditional examples often are, but interior needs and garage placement can lead to more flexible compositions."), ("Can the first floor be opened up?", "Yes. Many Colonial layouts can be adapted for larger kitchen, dining, and gathering spaces.")]},
    "cape-cod-house-plans": {"title": "Cape Cod House Plans", "meta": "Browse Cape Cod house plans inspired by compact, practical New England homes.", "intro": "Cape Cod homes pair compact massing with steep roof forms that suit New England architectural traditions. Plans may begin with first-floor living and use dormers or future upper-level space to add bedrooms, work areas, or flexible rooms.", "style": "cape-cod", "faqs": [("Can upstairs space be finished later?", "Some Cape layouts can support phased upper-level finishing when planned into the structure and permit set."), ("Can dormers be changed?", "Dormer size and placement are common customization items, subject to structure and exterior proportions.")]},
    "modern-farmhouse-plans": {"title": "Modern Farmhouse Plans", "meta": "Explore modern farmhouse plans with welcoming porches, practical layouts, and clean New England detailing.", "intro": "Modern farmhouse plans combine familiar gabled forms and welcoming outdoor spaces with open, practical interiors. The strongest versions balance contemporary living with restrained exterior details that will age well.", "style": "modern-farmhouse", "faqs": [("What defines a modern farmhouse?", "Common traits include simple gables, porches, strong indoor-outdoor connections, and an informal central living area."), ("Can the exterior be made more traditional?", "Yes. Materials, trim, windows, porch details, and roof elements can shift the character significantly.")]},
    "adu-carriage-house-plans": {"title": "ADU and Carriage House Plans", "meta": "Browse ADU and carriage house plans for flexible living, guests, family, or rental use.", "intro": "Accessory dwelling units and carriage houses can add flexible living space without functioning like a full-size primary home. Successful plans respond closely to zoning, access, privacy, parking, utilities, and the relationship to the main residence.", "filters": {"is_adu": True}, "faqs": [("Are ADUs allowed everywhere?", "No. Zoning, dimensional, parking, occupancy, and utility rules must be checked for the property."), ("Can an ADU sit above a garage?", "Often, but stairs, structure, fire separation, height, and local rules shape the design.")]},
    "narrow-lot-house-plans": {"title": "Narrow-Lot House Plans", "meta": "Find narrow-lot house plans that use limited frontage efficiently without sacrificing daily function.", "intro": "Narrow lots reward careful circulation, window placement, garage strategy, and room proportions. These plans concentrate useful living space within a slimmer footprint and can be adapted after confirming survey, setback, access, and utility constraints.", "filters": {"narrow_lot": True}, "faqs": [("What counts as a narrow lot?", "It depends on local setbacks and the buildable width, not only the total frontage."), ("Should I have a survey first?", "Yes. A current survey is one of the most useful inputs before adapting a plan to a constrained lot.")]},
    "one-story-house-plans": {"title": "One-Story House Plans", "meta": "Browse one-story house plans for accessible, connected, and efficient daily living.", "intro": "One-story plans keep bedrooms, shared spaces, and everyday functions on a single level. They can support aging in place and simple circulation, though wider footprints make lot dimensions and roof development especially important.", "filters": {"stories": 1}, "faqs": [("Are one-story homes easier to build?", "They simplify some circulation and framing decisions but often require more foundation and roof area for the same square footage."), ("Can they include bonus space?", "Yes. Some designs use space over a garage or within the roof while retaining primary living on one floor.")]},
    "small-house-plans-under-1500-square-feet": {"title": "House Plans Under 1,500 Square Feet", "meta": "Explore efficient house plans under 1,500 square feet for smaller households, lots, and budgets.", "intro": "Smaller homes work best when circulation is minimized and each room has a clear purpose. These plans prioritize useful storage, daylight, connected living spaces, and comfortable proportions rather than simply compressing a larger layout.", "filters": {"square_footage__lte": 1500}, "faqs": [("Can a small plan still have three bedrooms?", "Yes, but storage, room size, circulation, and shared-space priorities need careful balance."), ("Does smaller always mean less expensive?", "Usually less area helps, but foundation type, complexity, finishes, site work, and local costs remain major factors.")]},
    "first-floor-primary-suite-plans": {"title": "House Plans With First-Floor Primary Suites", "meta": "Browse house plans featuring first-floor primary suites for privacy, convenience, and long-term flexibility.", "intro": "A first-floor primary suite separates everyday owner spaces from secondary bedrooms and can support long-term living without relying on stairs. Good layouts balance privacy with convenient access to laundry, living areas, and outdoor space.", "filters": {"first_floor_primary": True}, "faqs": [("Does the whole home need to be one story?", "No. Secondary bedrooms and flexible rooms can remain upstairs while the primary suite stays on the main level."), ("Can an existing plan be revised this way?", "Often, although the footprint, plumbing, circulation, and exterior may need meaningful changes.")]},
}


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
    stories_raw = request.GET.get("stories") or ""
    garage_raw = request.GET.get("garage") or ""
    max_width_raw = request.GET.get("max_width") or ""
    max_depth_raw = request.GET.get("max_depth") or ""
    selected_features = [key for key in request.GET.getlist("features") if key in FEATURE_FILTERS]

    # Apply filters
    if q_raw:
        qs = qs.filter(
            Q(plan_number__icontains=q_raw)
            | Q(plan_name__icontains=q_raw)
            | Q(description__icontains=q_raw)
            | Q(key_features__icontains=q_raw)
        )

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
    if stories_raw in STORY_FILTER_CHOICES:
        qs = qs.filter(stories__gte=3 if stories_raw == "3+" else _as_int(stories_raw))
    if garage_raw in GARAGE_FILTER_CHOICES:
        qs = qs.filter(garage_stalls__gte=3 if garage_raw == "3+" else _as_int(garage_raw))
    max_width = _as_int(max_width_raw)
    max_depth = _as_int(max_depth_raw)
    if max_width is not None:
        qs = qs.filter(house_width_in__lte=max_width * 12)
    if max_depth is not None:
        qs = qs.filter(house_depth_in__lte=max_depth * 12)
    for feature in selected_features:
        qs = qs.filter(**{FEATURE_FILTERS[feature][0]: True})

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
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    ctx = {
        "page": {"title": "Plans", "description": "Explore our house plans."},
        "plans": page_obj,
        "plan_count": qs.count(),
        "styles": styles,
        "categories": CATEGORY_PAGES,
        "active_style": active_style,
        "sqft_choices": SQFT_CHOICES,
        "bed_choices": BED_FILTER_CHOICES,
        "bath_choices": BATH_FILTER_CHOICES,
        "story_choices": STORY_FILTER_CHOICES,
        "garage_choices": GARAGE_FILTER_CHOICES,
        "feature_choices": [(key, label) for key, (_, label) in FEATURE_FILTERS.items()],
        "has_filters": bool(request.GET),
        "filter_query": query_without_page.urlencode(),
        "filters": {
            "style": active_style.slug if active_style else style_q,
            "q": q_raw,
            "min_sqft": min_sqft_raw,
            "max_sqft": max_sqft_raw,
            "beds": beds_raw,
            "baths": baths_raw,
            "sort": sort,
            "stories": stories_raw,
            "garage": garage_raw,
            "max_width": max_width_raw,
            "max_depth": max_depth_raw,
            "features": selected_features,
        },
        "saved_plan_ids": get_saved_plan_ids(request),
        "comparison_plan_ids": get_comparison_plan_ids(request),
    }
    return render(request, "plans/plans.html", ctx)


def plan_category(request: HttpRequest, category_slug: str) -> HttpResponse:
    category = CATEGORY_PAGES.get(category_slug)
    if not category:
        from django.http import Http404
        raise Http404("Plan category not found")

    qs = Plans.objects.filter(is_available=True).prefetch_related("house_styles")
    if category.get("style"):
        qs = qs.filter(house_styles__slug=category["style"])
    if category.get("filters"):
        qs = qs.filter(**category["filters"])
    qs = qs.distinct().order_by("-is_featured", "-created_date")
    page_obj = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(request, "plans/category.html", {
        "category": category,
        "plans": page_obj,
        "saved_plan_ids": get_saved_plan_ids(request),
        "comparison_plan_ids": get_comparison_plan_ids(request),
    })


def plan_finder(request: HttpRequest) -> HttpResponse:
    return render(request, "plans/plan_finder.html", {
        "styles": HouseStyleModel.objects.all().order_by("style_name"),
        "feature_choices": [(key, label) for key, (_, label) in FEATURE_FILTERS.items()],
    })


def plan_detail(request: HttpRequest, house_style_slug: str, plan_slug: str) -> HttpResponse:
    """
    Single plan detail + gallery + request changes form (no user deps).
    """
    plan = get_object_or_404(
        Plans.objects.prefetch_related("house_styles", "faqs"),
        house_styles__slug=house_style_slug,
        slug=plan_slug,
    )

    # Track this plan as recently viewed
    from . import session_utils
    session_utils.track_viewed_plan(request, plan.id)

    images = list(PlanGallery.objects.filter(plan=plan).order_by("order", "id"))
    base_price: Decimal = plan.plan_price or Decimal("0")
    related_plans = (
        Plans.objects.filter(is_available=True, house_styles__in=plan.house_styles.all())
        .exclude(pk=plan.pk)
        .prefetch_related("house_styles")
        .distinct()[:3]
    )
    plan_faqs = [
        {
            "question": "What is included with this house plan?",
            "answer": plan.package_contents
            or "The exact drawing set and delivery format will be confirmed before purchase so you know what is included for this plan.",
        },
        {
            "question": "Can this plan be modified?",
            "answer": "Yes. Common changes can be reviewed and quoted separately before the plan package is finalized.",
        },
        {
            "question": "Can I build this plan more than once?",
            "answer": "The standard purchase includes a single-use license to construct one home. Additional builds require additional permission or licensing.",
        },
        {
            "question": "Will this plan meet requirements for my property?",
            "answer": "Site, zoning, code, energy, engineering, and professional-stamp requirements vary by location and must be confirmed for the project address.",
        },
    ]
    plan_faqs.extend(
        {"question": faq.question, "answer": faq.answer}
        for faq in plan.faqs.all()
    )
    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {"@type": "Answer", "text": faq["answer"]},
            }
            for faq in plan_faqs
        ],
    }

    ctx = {
        "plan": plan,
        "images": images,
        "base_price": base_price,
        "page": {"title": f"Plan {plan.plan_number}"},
        "comment_form": PlanCommentForm(),
        "recaptcha_site_key": (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip(),
        "is_saved": session_utils.is_plan_saved(request, plan.id),
        "is_in_comparison": session_utils.is_in_comparison(request, plan.id),
        "related_plans": related_plans,
        "plan_faqs": plan_faqs,
        "plan_faq_schema": (
            json.dumps(faq_schema)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        ),
    }
    return render(request, "plans/plan_detail.html", ctx)


def search(request: HttpRequest) -> HttpResponse:
    """Simple search endpoint; reuses list template with a keyword filter."""
    q_raw = (request.GET.get("q") or "").strip()
    qs = Plans.objects.filter(is_available=True).prefetch_related("house_styles")
    if q_raw:
        qs = qs.filter(
            Q(plan_number__icontains=q_raw)
            | Q(plan_name__icontains=q_raw)
            | Q(description__icontains=q_raw)
            | Q(key_features__icontains=q_raw)
        )

    paginator = Paginator(qs.order_by("-created_date"), 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    ctx = {
        "page": {"title": "Plans", "description": f"Search results for '{q_raw}'"},
        "plans": page_obj,
        "plan_count": qs.count(),
        "styles": HouseStyleModel.objects.all().order_by("style_name"),
        "active_style": None,
        "sqft_choices": SQFT_CHOICES,
        "bed_choices": BED_FILTER_CHOICES,
        "bath_choices": BATH_FILTER_CHOICES,
        "story_choices": STORY_FILTER_CHOICES,
        "garage_choices": GARAGE_FILTER_CHOICES,
        "feature_choices": [(key, label) for key, (_, label) in FEATURE_FILTERS.items()],
        "has_filters": True,
        "filter_query": query_without_page.urlencode(),
        "filters": {"q": q_raw, "sort": "newest", "min_sqft": "", "max_sqft": "", "beds": "", "baths": "", "style": "", "stories": "", "garage": "", "max_width": "", "max_depth": "", "features": []},
        "saved_plan_ids": get_saved_plan_ids(request),
        "comparison_plan_ids": get_comparison_plan_ids(request),
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
                    "- Provost Home Design"
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
    shared_slugs = []
    for raw_slug in (request.GET.get("plans") or "").split(","):
        slug = raw_slug.strip()
        if slug and slug not in shared_slugs:
            shared_slugs.append(slug)
        if len(shared_slugs) == 4:
            break
    comparison_ids = session_utils.get_comparison_plan_ids(request)

    if shared_slugs:
        plans = Plans.objects.filter(
            slug__in=shared_slugs,
            is_available=True,
        ).prefetch_related("house_styles", "images")
        plans_dict = {plan.slug: plan for plan in plans}
        plans_ordered = [plans_dict[slug] for slug in shared_slugs if slug in plans_dict]
    elif comparison_ids:
        plans = Plans.objects.filter(
            id__in=comparison_ids,
            is_available=True
        ).prefetch_related("house_styles", "images")
        
        # Sort by session order
        plans_dict = {p.id: p for p in plans}
        plans_ordered = [plans_dict[pid] for pid in comparison_ids if pid in plans_dict]
    else:
        plans_ordered = []

    share_query = urlencode({"plans": ",".join(plan.slug for plan in plans_ordered)})
    share_url = request.build_absolute_uri(
        f"{reverse('plans:compare_plans')}?{share_query}"
    ) if plans_ordered else ""
    context = {
        "page": {"title": "Compare Plans", "description": "Side-by-side plan comparison"},
        "plans": plans_ordered,
        "comparison_count": len(comparison_ids),
        "share_url": share_url,
        "is_shared_comparison": bool(shared_slugs),
    }
    return render(request, "plans/compare.html", context)


@require_POST
def clear_comparison_view(request: HttpRequest) -> HttpResponse:
    """Clear all plans from comparison."""
    session_utils.clear_comparison(request)
    messages.success(request, "Comparison cleared")
    return redirect("plans:plan_list")
