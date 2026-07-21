# pages/views.py
from __future__ import annotations
from time import time
from django.core.exceptions import ValidationError
from django_ratelimit.decorators import ratelimit


import contextlib
import json
import logging
import re
from typing import Iterable

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from core.utils import get_client_ip, verify_recaptcha_v3
from .forms import ContactForm, NewHouseForm, TestimonialForm, WebDesignInquiryForm
from .models import (
    ContactMessage,
    InquiryAttachment,
    ProjectInquiry,
    Testimonial,
    AboutPage,
    SiteSettings,
    WebDesignInquiry,
    WEB_PROJECT_TYPE_CHOICES,
    PricingPage,
    AffiliateProduct,
    AffiliateCategory,
    ProjectCaseStudy,
)
from plans.models import Plans, HouseStyle
from plans.session_utils import get_saved_plan_ids, get_comparison_plan_ids
from django.core.paginator import Paginator

logger = logging.getLogger(__name__)

SERVICE_PAGES = {
    "custom-home-design-massachusetts": {
        "title": "Custom Home Design in Massachusetts",
        "meta": "Custom residential home design and permit drawing services for Massachusetts homeowners and builders.",
        "eyebrow": "Massachusetts Residential Design",
        "intro": "Create a home around your site, priorities, and budget with direct guidance from concept through a coordinated residential drawing set. The process accounts for New England construction practices and the practical questions that arise during local permit review.",
        "highlights": ["Site- and lifestyle-driven floor plans", "Exterior elevations and roof development", "Permit-set coordination", "Builder-friendly framing information"],
        "faqs": [("When should I get in touch?", "Early concept work is ideal, especially before committing to a floor plan or final construction budget."), ("Are local requirements the same in every town?", "No. The applicable code, zoning, site, and documentation requirements must be confirmed for the project address.")],
    },
    "custom-home-design-rhode-island": {
        "title": "Custom Home Design in Rhode Island",
        "meta": "Custom house plans and residential permit drawings for Rhode Island homeowners, builders, and contractors.",
        "eyebrow": "Rhode Island Residential Design",
        "intro": "Develop a custom Rhode Island home with a clear design process centered on function, buildability, and architectural character. Plans are prepared with regional construction experience and coordinated around the needs of the owner, builder, and permitting path.",
        "highlights": ["Custom floor-plan development", "Coastal and New England design context", "Construction-document coordination", "Plan revisions during design"],
        "faqs": [("Can you work with my builder?", "Yes. Builder input can be incorporated early so design decisions stay grounded in construction approach and budget."), ("Do projects require other professionals?", "Some sites and scopes require an architect, engineer, surveyor, or other specialist. Those needs are identified as early as practical.")],
    },
    "house-plan-modifications": {
        "title": "House Plan Modification Services",
        "meta": "Modify a stock house plan for your lot, lifestyle, builder, and New England project requirements.",
        "eyebrow": "Adapt a Plan You Already Like",
        "intro": "A strong stock plan can be an efficient starting point. I help homeowners and builders adjust room layouts, dimensions, garages, porches, rooflines, and exterior character while keeping the revised design coordinated and buildable.",
        "highlights": ["Floor-plan and room-layout changes", "Garage, porch, and entry revisions", "Exterior and roofline options", "Plan-set updates for the revised scope"],
        "faqs": [("Can any stock plan be modified?", "Most can, but the original file format, license, structural impact, and amount of change affect the best approach."), ("How is modification pricing determined?", "Pricing is based on the plan, requested changes, available source files, and required deliverables.")],
    },
    "additions-and-renovations": {
        "title": "Residential Addition and Renovation Plans",
        "meta": "Residential addition and renovation drawings for New England homes, homeowners, and contractors.",
        "eyebrow": "Improve the Home You Have",
        "intro": "Additions and renovations need to connect new ideas to an existing structure. The design process documents existing conditions, develops a practical layout, and coordinates the new exterior and construction drawings with the character of the home.",
        "highlights": ["Existing-condition and proposed plans", "Additions, dormers, and interior reconfiguration", "Exterior elevation coordination", "Clear drawings for pricing and permits"],
        "faqs": [("What should I provide first?", "Photos, available plans, a property survey, and a short description of what is not working are useful starting points."), ("Will an engineer be needed?", "Structural changes may require engineering. The need depends on the existing building and proposed work.")],
    },
    "residential-framing-plans": {
        "title": "Residential Framing Plans",
        "meta": "Practical residential framing plans coordinated for New England builders, contractors, and home projects.",
        "eyebrow": "Construction-Minded Documentation",
        "intro": "Framing drawings translate design intent into information a builder can use in the field. My background with residential construction, engineered wood, and truss coordination helps surface conflicts and clarify the structural layout before they become job-site questions.",
        "highlights": ["Floor and roof framing layouts", "Beam, header, and opening coordination", "Truss and engineered-wood coordination", "Builder and engineer collaboration"],
        "faqs": [("Are framing plans engineering?", "Framing plans communicate layout and design intent. Calculations or stamped structural engineering must be provided by a qualified engineer when required."), ("Can framing be added to an existing plan?", "Yes, after reviewing the architectural set, project location, structural approach, and builder requirements.")],
    },
    "permit-ready-house-plans": {
        "title": "Permit-Ready House Plans for New England",
        "meta": "Coordinated residential permit drawing sets for Massachusetts, Rhode Island, and New England projects.",
        "eyebrow": "Clear Residential Drawing Sets",
        "intro": "A permit submission needs more than an attractive floor plan. I prepare coordinated residential drawings that communicate the proposed work, dimensions, elevations, and construction intent, then help address reasonable drawing comments that arise during review.",
        "highlights": ["Dimensioned plans and exterior elevations", "Building sections and construction details", "Project-specific code information", "Coordination with required specialists"],
        "faqs": [("Does permit-ready guarantee approval?", "No designer can guarantee approval. Zoning, site conditions, local interpretation, and required outside professionals can affect a permit."), ("What is included in my set?", "The exact sheet list and deliverables are confirmed in the proposal because project and municipal requirements vary.")],
    },
    "builder-contractor-plan-services": {
        "title": "Plan Services for Builders and Contractors",
        "meta": "Residential plan, modification, framing, and construction-document support for New England builders and contractors.",
        "eyebrow": "A Practical Design Partner",
        "intro": "Builders and contractors need drawings that support estimating, coordination, permitting, and field decisions. I provide responsive residential design support grounded in hands-on knowledge of framing, trusses, engineered wood, and the construction process.",
        "highlights": ["Repeatable stock-plan adaptation", "Client-requested plan revisions", "Framing and truss coordination", "Permit and field revision support"],
        "faqs": [("Do you support repeat builder work?", "Yes. We can establish a consistent handoff and drawing process for recurring residential projects."), ("Can you coordinate directly with clients and trades?", "Yes, with clear roles and communication agreed at the start of the project.")],
    },
    "massachusetts-adu-plans": {
        "title": "Massachusetts ADU Plans",
        "meta": "Custom and adaptable accessory dwelling unit plans for Massachusetts properties, homeowners, and builders.",
        "eyebrow": "Accessory Dwelling Unit Design",
        "intro": "Develop an accessory dwelling unit around your property, household needs, and intended use. ADU design starts with the site and local requirements, then coordinates privacy, access, parking, utilities, structure, and a practical compact layout.",
        "highlights": ["Detached and attached ADU concepts", "Garage-apartment and carriage-house layouts", "Compact kitchens, baths, and storage", "Permit-set and consultant coordination"],
        "faqs": [("Can I use a stock ADU plan on my property?", "A stock plan can be a useful starting point, but site fit, zoning, access, utilities, and local submission requirements must be confirmed."), ("Can an ADU be designed for aging in place?", "Yes. Single-level circulation, accessible entries, bathroom clearances, and adaptable features can be considered from the beginning.")],
    },
    "new-england-house-plans": {
        "title": "New England House Plans",
        "meta": "Stock, modified, and custom house plans informed by New England construction, climate, and architectural character.",
        "eyebrow": "Regionally Informed Residential Design",
        "intro": "Choose a house plan with the region in mind. Provost Home Design combines familiar New England forms with practical layouts, code-conscious documentation, and framing experience, then helps adapt the design to the site, builder, and permitting path.",
        "highlights": ["Ranch, Colonial, Cape, and farmhouse plans", "Snow, roof, and framing coordination", "Plan modifications for site and lifestyle", "Direct collaboration with the designer"],
        "faqs": [("What makes a plan suitable for New England?", "The site, climate, foundation, envelope, structural approach, local requirements, and builder practices all matter in addition to architectural style."), ("Can a national stock plan be adapted?", "Often, yes. The plan should be reviewed for the project location and revised where needed before permit submission or construction.")],
    },
}

SERVICE_RELATED_LINKS = {
    "custom-home-design-massachusetts": {"categories": ["ranch-house-plans", "colonial-house-plans", "modern-farmhouse-plans"], "resources": ["stock-plan-vs-custom-home-design", "what-to-have-before-contacting-home-designer", "do-i-need-an-architect-for-residential-project"]},
    "custom-home-design-rhode-island": {"categories": ["cape-cod-house-plans", "ranch-house-plans", "one-story-house-plans"], "resources": ["stock-plan-vs-custom-home-design", "what-is-included-in-residential-permit-set"]},
    "house-plan-modifications": {"categories": ["narrow-lot-house-plans", "first-floor-primary-suite-plans"], "resources": ["can-a-stock-house-plan-be-modified", "stock-plan-vs-custom-home-design"]},
    "additions-and-renovations": {"categories": ["ranch-house-plans", "cape-cod-house-plans"], "resources": ["what-to-have-before-contacting-home-designer", "what-is-included-in-residential-permit-set"]},
    "residential-framing-plans": {"categories": ["one-story-house-plans", "modern-farmhouse-plans"], "resources": ["what-is-included-in-a-framing-plan", "what-is-included-in-residential-permit-set"]},
    "permit-ready-house-plans": {"categories": ["one-story-house-plans", "small-house-plans-under-1500-square-feet"], "resources": ["what-is-included-in-residential-permit-set", "common-reasons-building-department-comments", "massachusetts-stretch-code-energy-design"]},
    "builder-contractor-plan-services": {"categories": ["ranch-house-plans", "colonial-house-plans"], "resources": ["what-is-included-in-a-framing-plan", "can-a-stock-house-plan-be-modified"]},
    "massachusetts-adu-plans": {"categories": ["adu-carriage-house-plans", "small-house-plans-under-1500-square-feet"], "resources": ["massachusetts-adu-planning-considerations", "what-to-have-before-contacting-home-designer", "what-is-included-in-residential-permit-set"]},
    "new-england-house-plans": {"categories": ["ranch-house-plans", "colonial-house-plans", "cape-cod-house-plans"], "resources": ["stock-plan-vs-custom-home-design", "can-a-stock-house-plan-be-modified"]},
}

PLAN_CATEGORY_LABELS = {
    "ranch-house-plans": "Ranch House Plans",
    "colonial-house-plans": "Colonial House Plans",
    "cape-cod-house-plans": "Cape Cod House Plans",
    "modern-farmhouse-plans": "Modern Farmhouse Plans",
    "adu-carriage-house-plans": "ADU and Carriage House Plans",
    "narrow-lot-house-plans": "Narrow-Lot House Plans",
    "one-story-house-plans": "One-Story House Plans",
    "small-house-plans-under-1500-square-feet": "House Plans Under 1,500 Square Feet",
    "first-floor-primary-suite-plans": "Plans With First-Floor Primary Suites",
}

RESOURCE_RELATED_LINKS = {
    "what-is-included-in-residential-permit-set": ("permit-ready-house-plans", "one-story-house-plans"),
    "stock-plan-vs-custom-home-design": ("custom-home-design-massachusetts", "ranch-house-plans"),
    "can-a-stock-house-plan-be-modified": ("house-plan-modifications", "first-floor-primary-suite-plans"),
    "what-to-have-before-contacting-home-designer": ("custom-home-design-massachusetts", "modern-farmhouse-plans"),
    "what-is-included-in-a-framing-plan": ("residential-framing-plans", "ranch-house-plans"),
    "how-much-do-custom-house-plans-cost": ("custom-home-design-massachusetts", "colonial-house-plans"),
    "how-long-does-house-design-take": ("custom-home-design-rhode-island", "cape-cod-house-plans"),
    "how-to-choose-house-plan-for-narrow-lot": ("house-plan-modifications", "narrow-lot-house-plans"),
    "do-i-need-an-architect-for-residential-project": ("custom-home-design-massachusetts", "ranch-house-plans"),
    "massachusetts-adu-planning-considerations": ("massachusetts-adu-plans", "adu-carriage-house-plans"),
    "massachusetts-stretch-code-energy-design": ("permit-ready-house-plans", "modern-farmhouse-plans"),
    "common-reasons-building-department-comments": ("permit-ready-house-plans", "one-story-house-plans"),
}

RESOURCE_ARTICLES = {
    "what-is-included-in-residential-permit-set": {
        "title": "What Is Included in a Residential Permit Drawing Set?",
        "description": "A practical overview of the drawings commonly included in a residential permit set and the project-specific documents that may also be required.",
        "summary": "A coordinated permit set explains the proposed work clearly enough for review, pricing, and construction. The exact sheet list depends on the project and local requirements.",
        "reviewed": "July 2026",
        "sections": [
            ("Core architectural drawings", "A typical set may include a site or plot reference, dimensioned floor plans, exterior elevations, building sections, roof information, and construction details. The drawings should agree with one another and describe both the layout and the building envelope."),
            ("Project and code information", "Cover sheets commonly identify the project, drawing index, applicable design criteria, and general notes. Energy, zoning, life-safety, or accessibility information may be shown in the set or supplied through separate documents."),
            ("Documents outside the architectural set", "A survey, septic design, energy report, truss package, product approvals, or structural engineering may be required separately. Confirm the submission checklist with the authority having jurisdiction before filing."),
        ],
    },
    "stock-plan-vs-custom-home-design": {
        "title": "Stock House Plan or Custom Home Design?",
        "description": "Compare stock house plans, plan modifications, and custom home design based on your lot, priorities, schedule, and desired level of personalization.",
        "summary": "The best starting point is the one that fits both your site and the amount of change you need. A stock plan can save design time; a custom plan gives the site and household more influence from the beginning.",
        "reviewed": "July 2026",
        "sections": [
            ("When a stock plan works well", "Choose a stock plan when the overall size, footprint, exterior, and room relationships already fit your needs. Allow time to verify zoning, site fit, code requirements, and builder preferences before treating it as permit-ready."),
            ("When modifications are the middle ground", "Plan modifications are useful when the concept is right but several targeted changes are needed, such as resizing rooms, revising a garage, changing windows, or adapting the foundation."),
            ("When custom design is worth it", "Custom design is usually the stronger route for unusual sites, specific views, complex programs, accessibility priorities, or changes extensive enough that the original plan would no longer provide a meaningful shortcut."),
        ],
    },
    "can-a-stock-house-plan-be-modified": {
        "title": "Can a Stock House Plan Be Modified?",
        "description": "Learn which stock-plan changes are typically practical, what affects modification scope, and what to confirm before design work begins.",
        "summary": "Most stock plans can be adjusted, but the right process depends on licensing, source files, structural impact, and how far the revised design moves from the original.",
        "reviewed": "July 2026",
        "sections": [
            ("Common plan modifications", "Frequent requests include moving walls, changing room sizes, adding a garage or porch, adjusting windows, revising the exterior style, and adapting a foundation to site or builder needs."),
            ("What makes a change more involved", "Moving stairs, changing bearing lines, reworking roof geometry, or significantly altering the footprint can affect many sheets. A coordinated update is more than changing one floor-plan drawing."),
            ("What to provide", "Share the complete plan set, proof of the right to modify it, editable source files if available, a marked-up request list, and known site or jurisdiction requirements. This supports an accurate scope before work starts."),
        ],
    },
    "what-to-have-before-contacting-home-designer": {
        "title": "What to Have Before Contacting a Home Designer",
        "description": "Prepare for a productive first residential design conversation with a concise project brief, site information, priorities, and budget context.",
        "summary": "You do not need every answer before reaching out. A few organized inputs make the first conversation more useful and help identify missing information early.",
        "reviewed": "July 2026",
        "sections": [
            ("Your project brief", "Outline who will use the home, desired rooms, approximate size, must-haves, preferences, and anything that does not work in your current space. Inspiration images are useful when paired with notes about what you like."),
            ("Property information", "Provide the address, survey or plot plan if available, site photos, and known zoning or septic information. Existing-home projects also benefit from prior drawings and photos of affected areas."),
            ("Budget and decision process", "A realistic construction range, target schedule, and list of decision-makers help keep early concepts grounded. If a builder is already involved, their input can be incorporated from the start."),
        ],
    },
    "what-is-included-in-a-framing-plan": {
        "title": "What Is Included in a Residential Framing Plan?",
        "description": "Understand the purpose of residential framing plans, the information they coordinate, and when separate structural engineering may be required.",
        "summary": "Framing plans communicate the intended arrangement of floors, roofs, openings, beams, and bearing conditions so architectural design and construction strategy stay coordinated.",
        "reviewed": "July 2026",
        "sections": [
            ("Typical framing information", "Depending on scope, drawings may show joist or truss direction, bearing lines, major beams, headers, openings, floor elevations, roof framing intent, and references to related sections or details."),
            ("Coordination value", "A framing layout helps identify conflicts among stairs, plumbing paths, open rooms, roof geometry, and mechanical needs before construction. It also gives builders, truss suppliers, and engineers a clearer coordination base."),
            ("Framing plans and engineering", "Framing drawings do not replace calculations or sealed structural documents when those are required. Project location, loading, spans, materials, and local review determine what must be designed by a qualified engineer."),
        ],
    },
    "how-much-do-custom-house-plans-cost": {
        "title": "What Affects the Cost of Custom House Plans?",
        "description": "Understand the scope, site, complexity, coordination, and deliverables that influence custom house-plan design fees.",
        "summary": "Design fees reflect more than square footage. The project type, complexity, available information, revision process, and required drawing set all shape the scope.",
        "reviewed": "July 2026",
        "sections": [
            ("Scope and starting point", "A new custom home, an addition, and a modification to an editable stock plan begin with different amounts of existing information. A clear written proposal should define the design phases, drawing deliverables, and included revisions."),
            ("Complexity and coordination", "Roof geometry, site constraints, structural spans, unusual spaces, multiple consultants, and jurisdiction-specific submissions can add coordination time even when two projects have similar square footage."),
            ("How to request a useful proposal", "Provide the project address, goals, approximate size, desired timing, available survey or existing drawings, and examples of the expected character. This makes it easier to compare scope rather than price alone."),
        ],
    },
    "how-long-does-house-design-take": {
        "title": "How Long Does the House Design Process Take?",
        "description": "Learn what influences the residential design timeline from initial scope through concepts, revisions, coordination, and permit drawings.",
        "summary": "The timeline depends on project readiness, complexity, decision speed, consultant coordination, and the level of documentation required.",
        "reviewed": "July 2026",
        "sections": [
            ("Early planning", "The process moves more efficiently when the property information, room priorities, budget context, and decision-makers are identified early. Missing survey or site information can delay meaningful design decisions."),
            ("Concepts and revisions", "Initial layouts establish the major relationships. Review time and the number or scale of revisions often have more schedule impact than drafting speed, so consolidated feedback is valuable."),
            ("Permit-set coordination", "After the design is approved, the drawings are coordinated for the agreed deliverables. Engineering, energy documentation, septic, truss work, or municipal prerequisites may follow separate schedules."),
        ],
    },
    "how-to-choose-house-plan-for-narrow-lot": {
        "title": "How to Choose a House Plan for a Narrow Lot",
        "description": "Evaluate buildable width, access, daylight, garage placement, circulation, and privacy when selecting a narrow-lot house plan.",
        "summary": "The frontage shown on a listing is not the same as buildable width. Start with reliable property information, then evaluate how the plan uses its limited footprint.",
        "reviewed": "July 2026",
        "sections": [
            ("Confirm the buildable area", "Use a current survey and verify setbacks, easements, access, utilities, and other site constraints before selecting a footprint. Small dimensional differences can determine whether a plan is practical."),
            ("Study light, privacy, and circulation", "Side windows may be limited by neighboring homes, so front, rear, courtyard, or high-window strategies can matter. Efficient stairs and hallways preserve more of the narrow footprint for usable rooms."),
            ("Plan the garage and outdoor connection", "Front-entry, rear-access, detached, and tandem garages affect the entire first-floor layout. Consider how vehicles, entries, trash, utilities, and outdoor living will work together."),
        ],
    },
    "do-i-need-an-architect-for-residential-project": {
        "title": "Do I Need an Architect for My Residential Project?",
        "description": "Learn when a residential designer may be an appropriate fit and when an architect, engineer, surveyor, or other specialist may be required.",
        "summary": "The right design team depends on the project type, location, complexity, and local submission requirements. Confirm the required professionals before committing to a drawing scope.",
        "reviewed": "July 2026",
        "sections": [
            ("Start with the project and jurisdiction", "Requirements vary by state, municipality, building type, size, use, and scope. A straightforward one- or two-family home may follow a different professional-design path than a mixed-use property, multifamily building, unusual structure, or project involving a change of use."),
            ("When other professionals may be needed", "A surveyor may be needed for property and site information, a professional engineer for structural or civil work, and an energy specialist for compliance documentation. An architect may be required by the applicable law or requested because the project would benefit from that professional scope."),
            ("Confirm responsibilities in writing", "Before design begins, identify who will prepare each drawing, calculation, report, and permit submission. The local building department can clarify its submission checklist, while each retained professional should define the limits of their own services."),
        ],
    },
    "massachusetts-adu-planning-considerations": {
        "title": "Massachusetts ADU Planning Considerations",
        "description": "Plan a Massachusetts accessory dwelling unit around the property, household, access, utilities, code requirements, and local permitting process.",
        "summary": "A successful ADU starts with more than a compact floor plan. Property constraints, access, privacy, parking, utilities, life safety, and local review all shape the practical design.",
        "reviewed": "July 2026",
        "sections": [
            ("Verify the property constraints", "Begin with reliable site information and current requirements for the project address. Review setbacks, easements, septic or sewer capacity, utilities, parking, emergency access, and whether an attached, detached, or conversion approach fits the property."),
            ("Design for independent daily use", "Consider a clear entrance, privacy from the primary home, daylight, storage, laundry, mechanical systems, and a comfortable connection to outdoor space. If aging in place is a goal, discuss step-free access, circulation widths, and adaptable bathroom features early."),
            ("Coordinate the permit path", "The required drawings and supporting documents depend on the proposed work and municipality. Confirm zoning and building submission requirements, utility coordination, energy documentation, and any need for survey, septic, fire-protection, or engineering services before finalizing the scope."),
        ],
    },
    "massachusetts-stretch-code-energy-design": {
        "title": "Massachusetts Stretch Code and Energy-Design Coordination",
        "description": "Understand why energy requirements should be coordinated early when planning a Massachusetts new home, addition, renovation, or ADU.",
        "summary": "Energy compliance affects assemblies, equipment, documentation, and sometimes the design itself. The applicable code and compliance path should be confirmed for the project address early.",
        "reviewed": "July 2026",
        "sections": [
            ("Confirm the applicable requirements", "Massachusetts energy requirements and municipal adoption can change. Determine which base, stretch, or specialized provisions apply to the project and permit date, then confirm the accepted compliance path with the authority having jurisdiction and the project energy professional."),
            ("Coordinate design decisions early", "Window area and performance, insulation assemblies, air sealing, mechanical systems, ventilation, water heating, and renewable-energy readiness can affect drawings, specifications, pricing, and available space. Early coordination reduces late redesign."),
            ("Plan the documentation handoff", "Clarify who will prepare energy models, compliance reports, testing, certificates, and field verification. Architectural drawings should align with the selected assemblies and equipment assumptions, but required analysis or certification must come from the appropriately qualified provider."),
        ],
    },
    "common-reasons-building-department-comments": {
        "title": "Common Reasons Residential Plans Receive Building-Department Comments",
        "description": "Reduce avoidable permit-review questions by coordinating site information, drawing consistency, code details, and supporting documents before submission.",
        "summary": "A review comment is not necessarily a design failure. Many comments result from missing information, conflicting sheets, or project-specific documents that were not included in the submission.",
        "reviewed": "July 2026",
        "sections": [
            ("Incomplete project information", "Comments often request clearer property data, scope descriptions, code references, dimensions, elevations, sections, or construction details. Use the municipality's current checklist and make sure the application and drawings describe the same work."),
            ("Coordination conflicts", "Floor plans, elevations, sections, structural information, energy documents, and manufacturer layouts must agree. Changes made on one sheet but not carried through the set can create questions about heights, openings, bearing, egress, or assemblies."),
            ("Missing supporting documents", "The permit package may also need a survey, septic approval, energy report, structural calculations, truss documents, product information, or approvals from other departments. Confirm who owns each deliverable and respond to comments as one coordinated package."),
        ],
    },
}

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

THROTTLE_WINDOW_SECONDS = int(getattr(settings, "FORM_THROTTLE_WINDOW_SECONDS", 600))
THROTTLE_MAX_ATTEMPTS = int(getattr(settings, "FORM_THROTTLE_MAX_ATTEMPTS", 3))
THROTTLE_BURST_WINDOW_SECONDS = int(getattr(settings, "FORM_THROTTLE_BURST_WINDOW_SECONDS", 60))
THROTTLE_BURST_MAX_ATTEMPTS = int(getattr(settings, "FORM_THROTTLE_BURST_MAX_ATTEMPTS", 1))

def _too_many_recent_submissions(request: HttpRequest, form_type: str = "contact") -> bool:
    """
    Check and update server-side throttle per IP and form type.
    Uses cache counters so bots cannot bypass by dropping session cookies.
    """
    ip = get_client_ip(request) or "unknown"
    safe_form_type = "".join(ch for ch in (form_type or "contact").lower() if ch.isalnum() or ch in ("-", "_"))

    # Wider window limit, e.g. max 3 submissions / 10 minutes per IP per form.
    wide_key = f"form_throttle:{safe_form_type}:{ip}:wide"
    if cache.add(wide_key, 1, timeout=THROTTLE_WINDOW_SECONDS):
        wide_count = 1
    else:
        try:
            wide_count = cache.incr(wide_key)
        except ValueError:
            cache.set(wide_key, 1, timeout=THROTTLE_WINDOW_SECONDS)
            wide_count = 1

    # Burst limit, e.g. max 1 submission / 60 seconds per IP per form.
    burst_key = f"form_throttle:{safe_form_type}:{ip}:burst"
    if cache.add(burst_key, 1, timeout=THROTTLE_BURST_WINDOW_SECONDS):
        burst_count = 1
    else:
        try:
            burst_count = cache.incr(burst_key)
        except ValueError:
            cache.set(burst_key, 1, timeout=THROTTLE_BURST_WINDOW_SECONDS)
            burst_count = 1

    return wide_count > THROTTLE_MAX_ATTEMPTS or burst_count > THROTTLE_BURST_MAX_ATTEMPTS

# ----- Views ----------------------------------------------------------------

def home(request: HttpRequest) -> HttpResponse:
    recent_plans = (
        Plans.objects
        .filter(is_available=True)
        .prefetch_related("house_styles")
        .only(
            "id", "slug", "plan_number", "plan_price", "square_footage",
            "bedrooms", "bathrooms", "main_image", "created_date"
        )
        .order_by("-is_featured", "-created_date")[:3]
    )

    recent_testimonials = list(
        Testimonial.objects
        .filter(approved=True, consent_to_publish=True)
        .only("id", "name", "rating", "message", "created_at")
        .order_by("-created_at")[:3]
    )

    # NEW: expose a few styles for the chips
    house_styles = HouseStyle.objects.only("slug", "style_name").order_by("style_name")[:8]

    affiliate_products = list(
        AffiliateProduct.objects.filter(category=AffiliateCategory.HOME_DESIGN, is_active=True)[:8]
    )
    featured_case_studies = list(
        ProjectCaseStudy.objects.filter(is_published=True, is_featured=True)[:3]
    )

    return render(
        request,
        "pages/home.html",
        {
            "recent_plans": recent_plans,
            "recent_testimonials": recent_testimonials,
            "house_styles": house_styles,
            "affiliate_products": affiliate_products,
            "featured_case_studies": featured_case_studies,
            "saved_plan_ids": get_saved_plan_ids(request),
            "comparison_plan_ids": get_comparison_plan_ids(request),
        },
    )

@ratelimit(key="ip", rate="5/m", block=True)
def contact(request: HttpRequest) -> HttpResponse:
    testimonial_page = bool(getattr(request, "testimonial_page", False))
    testimonial_redirect = "pages:submit_testimonial" if testimonial_page else "pages:contact"
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
            span = "-"
        hours_display.append({"day": DAY_LABELS.get(h.day, h.day), "span": span})  # type: ignore

    # Email recipients
    to_emails = (
        _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
        or [email or getattr(settings, "DEFAULT_FROM_EMAIL", "")]
    )
    bcc_emails = _as_list(getattr(settings, "CONTACT_BCC_EMAILS", None))

    action = request.POST.get("action", "").strip().lower() if request.method == "POST" else ""
    is_contact_post = action == "send_message" or any(k.startswith("contact-") for k in request.POST.keys())
    is_testimonial_post = action == "submit_testimonial" or any(k.startswith("testimonial-") for k in request.POST.keys())

    # Forms - only bind the form that's being submitted
    if request.method == "POST" and is_contact_post:
        contact_form = ContactForm(request.POST, prefix="contact")
        tform = TestimonialForm(prefix="testimonial")
    elif request.method == "POST" and is_testimonial_post:
        contact_form = ContactForm(prefix="contact")
        tform = TestimonialForm(request.POST, prefix="testimonial")
    else:
        contact_form = ContactForm(prefix="contact")
        tform = TestimonialForm(prefix="testimonial")

    # Seed the timing token on GET for contact form
    if request.method != "POST":
        request.session["contact_started_ts"] = time()
        request.session["testimonial_started_ts"] = time()

    if request.method == "POST":
        # Contact submission
        if is_contact_post:
            # 1) Contact-specific throttle
            if _too_many_recent_submissions(request, "contact"):
                msg = "You're sending messages too quickly. Please wait a minute and try again."
                if _is_htmx(request):
                    return _htmx_status(request, "warning", msg)
                messages.warning(request, msg)
                return redirect(testimonial_redirect)

            # 2) "Too fast" submission (likely bot)
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
                return redirect(testimonial_redirect)

            # refresh seed to avoid reusing the same timestamp
            request.session["contact_started_ts"] = time()

            # reCAPTCHA v3 verification (enforced if secret is configured)
            recaptcha_ok, score = verify_recaptcha_v3(request, expected_action="contact_form")
            if not recaptcha_ok:
                msg = "Spam detection failed. Please try again."
                if _is_htmx(request):
                    return _htmx_status(request, "error", msg)
                messages.error(request, msg)
                return redirect(testimonial_redirect)

            if not request.POST.get(f"{contact_form.prefix}-terms_accepted") and hasattr(contact_form, "fields") and "terms_accepted" in contact_form.fields:
                contact_form.add_error("terms_accepted", "You must accept the Terms & Conditions.")

            if contact_form.is_valid():
                cd = contact_form.cleaned_data
                sub = cd.get("subject") or f"Contact request from {cd['name']}"
                message_id_display = "-"

                with contextlib.suppress(Exception):
                    ContactMessage.objects.create(
                        name=cd["name"],
                        email=cd["email"],
                        phone=cd.get("phone", ""),
                        subject=sub,
                        message=cd["message"],
                        ip_address=get_client_ip(request),
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        referer=request.META.get("HTTP_REFERER", ""),
                    )

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
                    logger.info(f"Contact form email sent: From {cd['email']} ({cd['name']}), To: {to_emails}")
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
                        "- The Team"
                    )
                    ack = EmailMultiAlternatives(
                        subject="Thanks - we received your message",
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
                return redirect(testimonial_redirect)

            # invalid
            request.session["contact_started_ts"] = time()  # Reset timer for retry
            if _is_htmx(request):
                return _htmx_status(request, "error", "Please correct the errors below and resubmit.")
            if "terms_accepted" in contact_form.errors:
                messages.error(request, "Please accept the Terms & Conditions to continue.")
            else:
                messages.error(request, "Please correct the errors below.")

        # Testimonial submission
        if is_testimonial_post:
            # 1) Testimonial-specific throttle
            if _too_many_recent_submissions(request, "testimonial"):
                msg = "You're sending testimonials too quickly. Please wait a minute and try again."
                if _is_htmx(request):
                    return _htmx_status(request, "warning", msg)
                messages.warning(request, msg)
                return redirect(testimonial_redirect)

            # 2) "Too fast" submission (likely bot)
            started = float(request.session.get("testimonial_started_ts", 0))
            if time() - started < 2.0:
                # reset seed for the next legit attempt
                request.session["testimonial_started_ts"] = time()
                logger.info(
                    "Testimonial spam gate tripped: too_fast ip=%s ua=%s",
                    request.META.get("REMOTE_ADDR"),
                    request.META.get("HTTP_USER_AGENT"),
                )
                if _is_htmx(request):
                    return _htmx_status(request, "error", "Spam protection triggered. Please try again.")
                messages.error(request, "Spam protection triggered. Please try again.")
                return redirect(testimonial_redirect)

            # refresh seed to avoid reusing the same timestamp
            request.session["testimonial_started_ts"] = time()

            # reCAPTCHA v3 verification (enforced if secret is configured)
            recaptcha_ok, score = verify_recaptcha_v3(request, expected_action="testimonial_form")
            if not recaptcha_ok:
                msg = "Spam detection failed. Please try again."
                if _is_htmx(request):
                    return _htmx_status(request, "error", msg)
                messages.error(request, msg)
                return redirect(testimonial_redirect)

            if not request.POST.get(f"{tform.prefix}-terms_accepted") and hasattr(tform, "fields") and "terms_accepted" in tform.fields:
                tform.add_error("terms_accepted", "You must accept the Terms & Conditions.")
            if tform.is_valid():
                cd = tform.cleaned_data
                t = Testimonial.objects.create(
                    name=cd["name"],
                    email=cd.get("email", ""),
                    role=cd.get("role") or "",
                    rating=int(cd["rating"]),
                    message=cd["message"],
                    consent_to_publish=cd["consent_to_publish"],
                    approved=False,
                )

                try:
                    admin_url = request.build_absolute_uri(reverse("admin:pages_testimonial_change", args=[t.pk]))
                    site_name = getattr(settings, "SITE_NAME", company)
                    ctx = {"t": t, "admin_url": admin_url, "site_name": site_name}

                    to_admin = (
                        _as_list(getattr(settings, "TESTIMONIAL_TO_EMAILS", None))
                        or _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
                        or [getattr(settings, "DEFAULT_FROM_EMAIL", "")]
                    )

                    if not to_admin or not to_admin[0]:
                        logger.warning(f"Testimonial admin email skipped: no recipients configured")
                    else:
                        subj = f"New testimonial submitted: {t.name or '(No name)'} ({t.rating}/5)"
                        text_body = render_to_string("pages/emails/testimonial_notify.txt", ctx)
                        html_body = render_to_string("pages/emails/testimonial_new.html", ctx)

                        em = EmailMultiAlternatives(
                            subject=subj,
                            body=text_body,
                            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                            to=to_admin,
                            reply_to=[t.email] if t.email else None,
                        )
                        em.attach_alternative(html_body, "text/html")
                        em.send(fail_silently=False)
                        logger.info(f"Testimonial notification sent: From {t.name} ({t.email or 'no email'}), Rating: {t.rating}/5, To: {to_admin}")
                except Exception:
                    logger.exception("Testimonial email send failed")
                
                # Send auto-ack to submitter if they provided email
                if t.email:
                    with contextlib.suppress(Exception):
                        ack_text = (
                            f"Hi {t.name},\n\n"
                            "Thanks for sharing your experience with us! "
                            "Your testimonial has been received and will be reviewed shortly.\n\n"
                            "We appreciate you taking the time to provide feedback.\n\n"
                            "- Provost Home Design"
                        )
                        ack = EmailMultiAlternatives(
                            subject="Thanks for your testimonial",
                            body=ack_text,
                            from_email=getattr(settings, "AUTO_ACK_FROM_EMAIL", getattr(settings, "DEFAULT_FROM_EMAIL", None)),
                            to=[t.email],
                        )
                        ack.send(fail_silently=True)

                if _is_htmx(request):
                    return _htmx_status(request, "success", "Thanks! Your testimonial was submitted and will appear once approved.")
                messages.success(request, "Thanks! Your testimonial was submitted and will appear once approved.")
                return redirect(testimonial_redirect)

            # invalid testimonial form
            request.session["testimonial_started_ts"] = time()  # Reset timer for retry
            if _is_htmx(request):
                return _htmx_status(request, "error", "Please correct the errors in the testimonial form.")
            if "terms_accepted" in tform.errors:
                messages.error(request, "Please accept the Terms & Conditions to submit your testimonial.")
            else:
                messages.error(request, "Please correct the errors in the testimonial form.")

        if not is_contact_post and not is_testimonial_post:
            messages.error(request, "Invalid form submission.")
            return redirect("pages:contact")

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
        "recaptcha_site_key": (
            (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip()
            or (getattr(settings, "RECAPTCHA_PUBLIC_KEY", "") or "").strip()
        ),
    }
    template_name = "pages/testimonial_submit.html" if testimonial_page else "pages/contact.html"
    return render(request, template_name, context)


def submit_testimonial(request: HttpRequest) -> HttpResponse:
    request.testimonial_page = True
    return contact(request)

def get_started(request: HttpRequest) -> HttpResponse:
    selected_plan_id = request.POST.get("plan_id") if request.method == "POST" else request.GET.get("plan")
    intent = request.POST.get("intent", request.GET.get("intent", "project-inquiry"))
    selected_plan = None
    if selected_plan_id:
        selected_plan = Plans.objects.filter(pk=selected_plan_id, is_available=True).first()

    if request.method == "POST":
        # reCAPTCHA v3 verification (enforced if secret is configured)
        recaptcha_ok, score = verify_recaptcha_v3(request, expected_action="get_started")
        if not recaptcha_ok:
            messages.error(request, "Spam detection failed. Please try again.")
            return redirect("pages:get_started")

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

            # Wrap both saves so transaction.on_commit in the post_save signal
            # fires after attachments are committed, ensuring the notification
            # email includes all uploaded files.
            with transaction.atomic():
                inquiry = ProjectInquiry.objects.create(
                    project_type=cd.get("project_type") or "",
                    project_location=cd.get("project_location") or "",
                    approximate_size=cd.get("approximate_size") or "",
                    project_timeline=cd.get("project_timeline") or "",
                    budget_range=cd.get("budget_range") or "",
                    consultation_requested=bool(cd.get("consultation_requested")),
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
                    additional_notes=(
                        f"Selected plan: {selected_plan.plan_number}\n"
                        f"Request type: {request.POST.get('intent', 'project inquiry')}\n\n"
                        if selected_plan else ""
                    ) + (cd.get("additional_notes") or ""),
                    terms_accepted=bool(cd.get("terms_accepted")),
                )
                for f in request.FILES.getlist("plan_files"):
                    InquiryAttachment.objects.create(inquiry=inquiry, file=f)

            messages.success(request, "Thanks! Your request was received. We’ll reach out soon.")
            return redirect("pages:project_thanks")

        messages.error(request, "Please fix the errors below.")
    else:
        initial = {}
        intent_project_types = {
            "buy-as-shown": "stock-plan",
            "plan-modifications": "plan-modification",
            "new-home": "new-home",
            "addition": "addition",
            "framing": "framing",
            "adu": "adu",
        }
        if intent in intent_project_types:
            initial["project_type"] = intent_project_types[intent]
        if intent == "consultation":
            initial["consultation_requested"] = True
        form = NewHouseForm(initial=initial)

    return render(
        request,
        "pages/get_started.html",
        {
            "page": {"title": "Get Started", "description": "Tell us about your project."},
            "form": form,
            "selected_plan": selected_plan,
            "intent": intent,
            "recaptcha_site_key": (
                (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip()
                or (getattr(settings, "RECAPTCHA_PUBLIC_KEY", "") or "").strip()
            ),
        },
    )


def project_thanks(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/project_thanks.html")

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
        # Render uses ManifestStaticFilesStorage in production. The old fallback
        # referenced files that are not in static/images, which raises a 500
        # before the page can render. Use the existing checked-in static image.
        photos = [static("images/michael.jpg")]

    knowledge_skills = ap.knowledge_skills_list() if ap else [
        "Schematic design", "Construction documents", "Residential code literacy",
        "Site planning", "Framing details", "MEP coordination (residential)",
        "Energy compliance basics", "Client communication",
    ]
    technology_terms = ("python", "django", "html", "css", "javascript", "bootstrap", "react", "web development", "postgres")
    residential_skills = [
        skill for skill in knowledge_skills
        if not any(term in skill.lower() for term in technology_terms)
    ]

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
        "knowledge_skills": residential_skills,
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

def services(request):
    return render(request, "pages/services.html")


def service_detail(request: HttpRequest, service_slug: str) -> HttpResponse:
    service = SERVICE_PAGES.get(service_slug)
    if not service:
        raise Http404("Service page not found")
    featured_plans = Plans.objects.filter(is_available=True).order_by("-is_featured", "-created_date")[:3]
    related = SERVICE_RELATED_LINKS.get(service_slug, {})
    related_categories = [
        {"slug": slug, "title": PLAN_CATEGORY_LABELS[slug]}
        for slug in related.get("categories", [])
        if slug in PLAN_CATEGORY_LABELS
    ]
    related_resources = [
        {"slug": slug, "title": RESOURCE_ARTICLES[slug]["title"]}
        for slug in related.get("resources", [])
        if slug in RESOURCE_ARTICLES
    ]
    return render(request, "pages/service_detail.html", {
        "service": service,
        "featured_plans": featured_plans,
        "related_categories": related_categories,
        "related_resources": related_resources,
    })


def resources(request: HttpRequest) -> HttpResponse:
    articles = [dict(article, slug=slug) for slug, article in RESOURCE_ARTICLES.items()]
    return render(request, "pages/resources.html", {
        "articles": articles,
        "has_case_studies": ProjectCaseStudy.objects.filter(is_published=True).exists(),
    })


def resource_detail(request: HttpRequest, resource_slug: str) -> HttpResponse:
    article = RESOURCE_ARTICLES.get(resource_slug)
    if not article:
        raise Http404("Resource not found")
    service_slug, category_slug = RESOURCE_RELATED_LINKS.get(
        resource_slug,
        ("custom-home-design-massachusetts", "ranch-house-plans"),
    )
    canonical_url = request.build_absolute_uri()
    resources_url = request.build_absolute_uri(reverse("pages:resources"))
    home_url = request.build_absolute_uri(reverse("pages:home"))
    about_url = request.build_absolute_uri(reverse("pages:about"))
    resource_schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Article",
                    "headline": article["title"],
                    "description": article["description"],
                    "dateModified": "2026-07-19",
                    "author": {
                        "@type": "Person",
                        "name": "Michael Provost",
                        "url": about_url,
                    },
                    "publisher": {
                        "@type": "Organization",
                        "@id": f"{home_url}#org",
                        "name": "Provost Home Design",
                    },
                    "mainEntityOfPage": {
                        "@type": "WebPage",
                        "@id": canonical_url,
                    },
                },
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": 1,
                            "name": "Resources",
                            "item": resources_url,
                        },
                        {
                            "@type": "ListItem",
                            "position": 2,
                            "name": article["title"],
                            "item": canonical_url,
                        },
                    ],
                },
            ],
        }
    ).replace("</", "<\\/")
    return render(
        request,
        "pages/resource_detail.html",
        {
            "article": article,
            "resource_schema": resource_schema,
            "resource_slug": resource_slug,
            "related_service": {
                "slug": service_slug,
                "title": SERVICE_PAGES[service_slug]["title"],
            },
            "related_category": {
                "slug": category_slug,
                "title": PLAN_CATEGORY_LABELS[category_slug],
            },
        },
    )


def case_study_list(request: HttpRequest) -> HttpResponse:
    studies = ProjectCaseStudy.objects.filter(is_published=True).prefetch_related("images")
    page_obj = Paginator(studies, 12).get_page(request.GET.get("page"))
    return render(request, "pages/case_study_list.html", {"case_studies": page_obj})


def case_study_detail(request: HttpRequest, case_study_slug: str) -> HttpResponse:
    case_study = get_object_or_404(
        ProjectCaseStudy.objects.prefetch_related("images"),
        slug=case_study_slug,
        is_published=True,
    )
    related_case_studies = ProjectCaseStudy.objects.filter(
        is_published=True,
        project_type=case_study.project_type,
    ).exclude(pk=case_study.pk)[:3]
    image_url = (
        request.build_absolute_uri(case_study.hero_image.url)
        if case_study.hero_image else ""
    )
    return render(request, "pages/case_study_detail.html", {
        "case_study": case_study,
        "related_case_studies": related_case_studies,
        "case_study_image_url": image_url,
    })


def web_design_legacy_redirect(request: HttpRequest) -> HttpResponse:
    return HttpResponsePermanentRedirect(f"{settings.WEB_DESIGN_URL}/")


def web_pricing_legacy_redirect(request: HttpRequest) -> HttpResponse:
    return HttpResponsePermanentRedirect(f"{settings.WEB_DESIGN_URL}/pricing/")


WEB_PROCESS_STEPS = [
    {"title": "Listen", "desc": "Clarify the audience, business goal, content, and practical constraints."},
    {"title": "Plan", "desc": "Agree on pages, features, responsibilities, timing, and a clear project scope."},
    {"title": "Build", "desc": "Design and develop in visible stages with useful review points along the way."},
    {"title": "Launch", "desc": "Test, deploy, hand off access, and define support after the site goes live."},
]

WEB_CASE_STUDIES = {
    "j-fisk-construction": {
        "title": "J. Fisk Construction",
        "category": "Local business website",
        "summary": "A focused, responsive website that gives a local construction company a credible online home and a direct path from service interest to contact.",
        "image": "images/fisk_truck.jpeg",
        "image_alt": "J. Fisk Construction truck and business branding",
        "live_url": "https://www.jfiskconstruction.com/",
        "client_need": "Create a professional web presence that clearly introduces the construction business, makes its services understandable, and gives prospective customers an easy way to reach out.",
        "challenge": "A contractor website has to build confidence quickly without burying visitors in copy. The structure needed to work on phones, keep service information easy to scan, and move interested visitors toward a real conversation.",
        "approach": [
            "Lead with the company and its construction work rather than technical features.",
            "Organize service information into a simple, mobile-friendly path.",
            "Use direct contact actions so visitors do not have to hunt for the next step.",
            "Keep the presentation practical and aligned with the existing business identity.",
        ],
        "deliverables": [
            "Responsive business website",
            "Service-focused page structure",
            "Contact and lead path",
            "Domain launch and production deployment",
        ],
        "outcome": "The finished site is live and gives J. Fisk Construction a clear destination to share with referrals and prospective customers across desktop and mobile devices.",
    },
    "provost-home-design-platform": {
        "title": "Provost Home Design Platform",
        "category": "Custom Django application",
        "summary": "A public website and operational platform built around house-plan discovery, structured inquiries, customer workflows, administration, and long-term growth.",
        "image": "images/hero-blueprint.jpg",
        "image_alt": "House plans representing the Provost Home Design web platform",
        "live_url": "https://www.provosthomedesign.com/",
        "client_need": "Support both sides of a residential-design business: help visitors discover plans and services while giving the owner practical tools to manage content, inquiries, customers, and ongoing operations.",
        "challenge": "A brochure site could not support a searchable plan catalog, saved plans, comparisons, structured project intake, protected workflows, media management, and business administration in one coordinated system.",
        "approach": [
            "Build the public plan catalog around useful search, filtering, comparison, and inquiry paths.",
            "Create structured forms and protected workflows instead of relying on unorganized email alone.",
            "Use Django administration and custom business tools for maintainable day-to-day control.",
            "Coordinate media storage, SEO, accessibility, analytics, security, and deployment as one platform.",
        ],
        "deliverables": [
            "Responsive public website and plan catalog",
            "Favorites, comparisons, and guided plan discovery",
            "Project intake and customer workflows",
            "Administration, media storage, email, analytics, and automation",
        ],
        "outcome": "The platform is live as the working digital foundation of Provost Home Design and can continue evolving as the residential business adds plans, content, and operational tools.",
    },
}


def web_design(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_design.html", {"process_steps": WEB_PROCESS_STEPS})


def web_services(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_services.html")


def web_work(request: HttpRequest) -> HttpResponse:
    studies = [dict(study, slug=slug) for slug, study in WEB_CASE_STUDIES.items()]
    return render(request, "pages/web_work.html", {"case_studies": studies})


def web_case_study(request: HttpRequest, case_study_slug: str) -> HttpResponse:
    case_study = WEB_CASE_STUDIES.get(case_study_slug)
    if not case_study:
        raise Http404("Web case study not found")
    canonical_url = request.build_absolute_uri()
    schema = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": case_study["title"],
                "description": case_study["summary"],
                "author": {"@type": "Person", "name": "Michael Provost"},
                "publisher": {"@id": f"{request.build_absolute_uri('/')}#org"},
                "mainEntityOfPage": {"@type": "WebPage", "@id": canonical_url},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Work", "item": request.build_absolute_uri(reverse("pages:web_work"))},
                    {"@type": "ListItem", "position": 2, "name": case_study["title"], "item": canonical_url},
                ],
            },
        ],
    }).replace("</", "<\\/")
    return render(request, "pages/web_case_study.html", {
        "case_study": case_study,
        "case_study_schema": schema,
    })


def web_about(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_about.html")


def web_thanks(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_thanks.html")


def web_terms(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_terms.html")


def web_privacy(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/web_privacy.html")


@ratelimit(key="ip", rate="5/m", block=True)
def web_contact(request: HttpRequest) -> HttpResponse:
    s = SiteSettings.load()
    company = s.company_name or getattr(settings, "COMPANY_NAME", "Provost Home Design")
    email = s.contact_email or getattr(settings, "CONTACT_EMAIL", "mike@provosthomedesign.com")
    logo_url = s.logo_url

    to_emails = (
        _as_list(getattr(settings, "CONTACT_TO_EMAILS", None))
        or [email or getattr(settings, "DEFAULT_FROM_EMAIL", "")]
    )

    if request.method == "POST":
        form = WebDesignInquiryForm(request.POST)
        if _too_many_recent_submissions(request, "web_design"):
            messages.warning(request, "You're sending messages too quickly. Please wait a minute and try again.")
            return render(request, "pages/web_contact.html", {
                "form": form,
                "recaptcha_site_key": (getattr(settings, "RECAPTCHA_SITE_KEY", "") or getattr(settings, "RECAPTCHA_PUBLIC_KEY", "")).strip(),
            }, status=429)

        started = float(request.session.get("web_design_started_ts", 0))
        if time() - started < 2.0:
            request.session["web_design_started_ts"] = time()
            messages.error(request, "Spam protection triggered. Please try again.")
            return render(request, "pages/web_contact.html", {
                "form": form,
                "recaptcha_site_key": (getattr(settings, "RECAPTCHA_SITE_KEY", "") or getattr(settings, "RECAPTCHA_PUBLIC_KEY", "")).strip(),
            }, status=400)
        request.session["web_design_started_ts"] = time()

        recaptcha_ok, _ = verify_recaptcha_v3(request, expected_action="web_design_form")
        if not recaptcha_ok:
            messages.error(request, "Spam detection failed. Please try again.")
            return render(request, "pages/web_contact.html", {
                "form": form,
                "recaptcha_site_key": (getattr(settings, "RECAPTCHA_SITE_KEY", "") or getattr(settings, "RECAPTCHA_PUBLIC_KEY", "")).strip(),
            }, status=400)

        if form.is_valid():
            cd = form.cleaned_data
            pt_label = dict(WEB_PROJECT_TYPE_CHOICES).get(cd.get("project_type", ""), cd.get("project_type", "")) or "Not specified"

            inquiry = WebDesignInquiry.objects.create(
                name=cd["name"],
                company_name=cd.get("company_name") or "",
                email=cd["email"],
                phone=cd.get("phone") or "",
                current_website=cd.get("current_website") or "",
                project_type=cd.get("project_type") or "",
                budget_range=cd.get("budget_range") or "",
                timeline=cd.get("timeline") or "",
                message=cd["message"],
                terms_accepted=bool(cd.get("terms_accepted")),
                ip_address=get_client_ip(request),
            )

            ctx = {
                "company": company,
                "logo_url": logo_url,
                "from_name": cd["name"],
                "company_name": cd.get("company_name", ""),
                "from_email": cd["email"],
                "from_phone": cd.get("phone", ""),
                "current_website": cd.get("current_website", ""),
                "project_type": pt_label,
                "budget_range": inquiry.get_budget_range_display() or "Not specified",
                "timeline": inquiry.get_timeline_display() or "Not specified",
                "message": cd["message"],
                "admin_url": request.build_absolute_uri(f"/admin/pages/webdesigninquiry/{inquiry.pk}/change/"),
                "request_url": request.build_absolute_uri(),
            }

            try:
                text_body = render_to_string("pages/emails/web_design_notification.txt", ctx)
                html_body = render_to_string("pages/emails/web_design_notification.html", ctx)
            except Exception:
                logger.exception("Missing/broken web design notification templates")
                messages.warning(request, "Your inquiry was saved, but the email notification could not be prepared. I will review it directly.")
                return redirect("pages:web_thanks")

            subject = f"[Web Design] New inquiry from {cd['name']}"
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                to=to_emails or [getattr(settings, "DEFAULT_FROM_EMAIL", "")],
                reply_to=[cd["email"]],
            )
            msg.attach_alternative(html_body, "text/html")
            try:
                msg.send(fail_silently=False)
                logger.info("Web design inquiry email sent: From %s (%s)", cd["email"], cd["name"])
            except Exception:
                logger.exception("Web design inquiry email send failed")
                messages.warning(request, "Your inquiry was saved, but the email notification was delayed. Please do not resubmit it.")
                return redirect("pages:web_thanks")

            ack_ctx = {
                **ctx,
                "web_url": getattr(
                    settings,
                    "WEB_DESIGN_URL",
                    request.build_absolute_uri("/"),
                ).rstrip("/"),
            }
            try:
                ack_text = render_to_string("pages/emails/web_design_ack.txt", ack_ctx)
                ack_html = render_to_string("pages/emails/web_design_ack.html", ack_ctx)
                acknowledgment = EmailMultiAlternatives(
                    subject="We received your web project inquiry",
                    body=ack_text,
                    from_email=getattr(
                        settings,
                        "AUTO_ACK_FROM_EMAIL",
                        getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    ),
                    to=[cd["email"]],
                )
                acknowledgment.attach_alternative(ack_html, "text/html")
                acknowledgment.send(fail_silently=False)
            except Exception:
                logger.exception(
                    "Web design inquiry acknowledgment failed for inquiry %s",
                    inquiry.pk,
                )

            messages.success(request, "Thanks! Your inquiry has been sent. I'll be in touch soon.")
            return redirect("pages:web_thanks")

        messages.error(request, "Please correct the errors below.")
    else:
        request.session["web_design_started_ts"] = time()
        form = WebDesignInquiryForm()

    return render(request, "pages/web_contact.html", {
        "form": form,
        "recaptcha_site_key": (
            (getattr(settings, "RECAPTCHA_SITE_KEY", "") or "").strip()
            or (getattr(settings, "RECAPTCHA_PUBLIC_KEY", "") or "").strip()
        ),
    })


def pricing(request: HttpRequest) -> HttpResponse:
    page = PricingPage.load()
    items = list(page.items.filter(is_active=True)) if page.is_published else []
    calc_items = [i for i in items if i.show_in_calculator]
    return render(request, "pages/pricing.html", {
        "page_obj": page,
        "items": items,
        "calc_items": calc_items,
    })


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
        f"Sitemap: {request.build_absolute_uri(reverse('image_sitemap'))}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def llms_txt(request):
    base = request.build_absolute_uri("/").rstrip("/")
    content = f"""# Provost Home Design

> Residential home design services and stock/custom house plans for New England homeowners and builders. Permit-ready, code-compliant architectural drawings for MA, RI, and NH.

## Site sections

- [Home]({base}/): Overview of services, featured house plans, and quick search.
- [Plan Finder]({base}/plans/finder/): Guided search for narrowing the house-plan catalog.
- [Resources]({base}/resources/): Residential planning guides covering plan selection, permit sets, modifications, framing, timelines, and site fit.
- [House Plans]({base}/plans/): Catalog of stock house plans — filter by style (Colonial, Cape, Ranch), square footage, bedrooms, bathrooms, and garage.
- [Services]({base}/services/): Custom home design, plan modifications, framing plans, permit sets, and exterior renderings.
- [About]({base}/about/): Background on Michael Provost, a residential designer with construction, framing, and engineered-wood experience.
- [Get Started]({base}/get-started/): Project intake form for new custom design inquiries.
- [Contact]({base}/contact/): Contact form, business hours, and location map.
- [Testimonials]({base}/testimonials/): Client reviews and testimonials.
- [Sitemap]({base}/sitemap.xml): Full XML sitemap.
- [Image Sitemap]({base}/image-sitemap.xml): Cover and gallery images associated with canonical house-plan pages.
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


def web_robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def web_llms_txt(request):
    base = request.build_absolute_uri("/").rstrip("/")
    content = f"""# Provost Home Design - Web Design

> Custom business websites and web applications. This service is temporarily hosted under the Provost Home Design name while a standalone brand is developed.

## Site sections

- [Home]({base}/): Local-business website positioning, process, and primary project paths.
- [Services]({base}/services/): Business websites, redesigns, and custom Django applications.
- [Work]({base}/work/): Selected live website and application projects.
- [J. Fisk Construction Case Study]({base}/work/j-fisk-construction/): Focused local-contractor business website.
- [Provost Home Design Platform Case Study]({base}/work/provost-home-design-platform/): Custom Django catalog and business platform.
- [About]({base}/about/): Michael Provost's approach to practical business websites and software.
- [Pricing]({base}/pricing/): Published web-service pricing and interactive estimates when available.
- [Contact]({base}/contact/): Web project inquiry form.
- [Privacy]({base}/privacy/): Web-services privacy notice.
- [Terms]({base}/terms/): General web-services and inquiry terms.
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")
