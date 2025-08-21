from __future__ import annotations
from decimal import Decimal

from django.core.mail import EmailMessage
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import HouseStyle as HouseStyleModel, Plans, PlanGallery
from .forms import PlanQuickForm, PlanCommentForm

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


def plan_list(request: HttpRequest, house_style_slug: str | None = None) -> HttpResponse:
    """
    Grid list of plans (3 across, paginated).
    Supports filters & sorting and allows selecting style via route OR ?style=<slug>.
    """
    styles = HouseStyleModel.objects.all().order_by("style_name")
    active_style = None

    qs = Plans.objects.filter(is_available=True).select_related("house_style")

    # Allow style filter from querystring first; fall back to /style/<slug>/ route.
    style_q = (request.GET.get("style") or "").strip()
    if style_q:
        active_style = get_object_or_404(HouseStyleModel, slug=style_q)
        qs = qs.filter(house_style=active_style)
    elif house_style_slug:
        active_style = get_object_or_404(HouseStyleModel, slug=house_style_slug)
        qs = qs.filter(house_style=active_style)

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
    }
    # IMPORTANT: never wrap ctx again (e.g. {"context": ctx}) — can create cycles
    return render(request, "plans/plans.html", ctx)


def plan_detail(request: HttpRequest, house_style_slug: str, plan_slug: str) -> HttpResponse:
    """
    Single plan detail + gallery + request changes form (no user deps).
    Defensive against recursion:
    - keep context flat/simple
    - don't pass nested dicts that contain themselves
    - avoid lazy objects that might capture outer context
    """
    plan = get_object_or_404(
        Plans.objects.select_related("house_style"),
        house_style__slug=house_style_slug,
        slug=plan_slug,
    )

    # Force a simple list to avoid any odd context-capturing/lazy behavior in templates
    images = list(PlanGallery.objects.filter(plan=plan).order_by("order", "id"))

    # Keep numbers as Decimal where possible; cast only for display in template if needed
    base_price: Decimal = plan.plan_price or Decimal("0")

    ctx = {
        "plan": plan,
        "images": images,
        "base_price": base_price,
        "page": {"title": f"Plan {plan.plan_number}"},
        "comment_form": PlanCommentForm(),
    }
    return render(request, "plans/plan_detail.html", ctx)


def search(request: HttpRequest) -> HttpResponse:
    """Simple search endpoint; reuses list template with a keyword filter."""
    q_raw = (request.GET.get("q") or "").strip()
    qs = Plans.objects.filter(is_available=True).select_related("house_style")
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
            messages.error(request, f"{field}: {', '.join(e for e in errs)}")  # type: ignore
        return redirect("plans:plan_list")

    plan: Plans = form.save(commit=False)

    # Auto-generate slug if missing
    if not getattr(plan, "slug", None):
        from django.utils.text import slugify
        plan.slug = slugify(str(plan.plan_number))

    # Default is_available True if not present
    if "is_available" not in form.cleaned_data:
        plan.is_available = True

    plan.save()
    form.save_m2m()

    # Handle gallery images (and set first as cover if none)
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
    plan = get_object_or_404(Plans, pk=plan_id)
    form = PlanCommentForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Please add your message (and a valid email if you want a reply).")
        return redirect(plan.get_absolute_url())

    # Guest-friendly: only use submitted fields
    name = form.cleaned_data.get("name", "")
    email = form.cleaned_data.get("email", "")
    message = form.cleaned_data["message"]

    subject = f"[Plan {plan.plan_number}] Change request"
    lines = [
        f"Plan: {plan.plan_number}",
        f"URL: {request.build_absolute_uri(plan.get_absolute_url())}",
    ]
    if name:
        lines.append(f"From: {name}")
    if email:
        lines.append(f"Email: {email}")
    lines.extend(("", "Message:", message))
    body = "\n".join(lines)

    to_emails = getattr(settings, "CONTACT_TO_EMAILS", None) or [getattr(settings, "DEFAULT_FROM_EMAIL", "")]
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)

    EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=to_emails,
        reply_to=[email] if email else None,
    ).send(fail_silently=False)

    messages.success(request, "Thanks! Your request has been emailed. We’ll follow up soon.")
    return redirect(plan.get_absolute_url())
