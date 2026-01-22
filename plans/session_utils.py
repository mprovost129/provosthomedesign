"""
Session-based utility functions for favorites and comparison features.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest
    from .models import Plans


def ensure_session_key(request: HttpRequest) -> str:
    """Ensure the request has a session key and return it."""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def get_saved_plan_ids(request: HttpRequest) -> list[int]:
    """Get list of saved plan IDs from session."""
    return request.session.get("saved_plans", [])


def add_to_saved_plans(request: HttpRequest, plan_id: int) -> bool:
    """Add a plan ID to saved plans. Returns True if added, False if already saved."""
    saved = get_saved_plan_ids(request)
    if plan_id not in saved:
        saved.append(plan_id)
        request.session["saved_plans"] = saved
        request.session.modified = True
        return True
    return False


def remove_from_saved_plans(request: HttpRequest, plan_id: int) -> bool:
    """Remove a plan ID from saved plans. Returns True if removed, False if not found."""
    saved = get_saved_plan_ids(request)
    if plan_id in saved:
        saved.remove(plan_id)
        request.session["saved_plans"] = saved
        request.session.modified = True
        return True
    return False


def is_plan_saved(request: HttpRequest, plan_id: int) -> bool:
    """Check if a plan is in saved plans."""
    return plan_id in get_saved_plan_ids(request)


def get_comparison_plan_ids(request: HttpRequest) -> list[int]:
    """Get list of comparison plan IDs from session."""
    return request.session.get("comparison_plans", [])


def add_to_comparison(request: HttpRequest, plan_id: int, max_plans: int = 4) -> tuple[bool, str | None]:
    """
    Add a plan ID to comparison list.
    Returns (success, error_message).
    """
    comparison = get_comparison_plan_ids(request)
    
    if plan_id in comparison:
        return False, "Plan already in comparison"
    
    if len(comparison) >= max_plans:
        return False, f"Maximum {max_plans} plans can be compared at once"
    
    comparison.append(plan_id)
    request.session["comparison_plans"] = comparison
    request.session.modified = True
    return True, None


def remove_from_comparison(request: HttpRequest, plan_id: int) -> bool:
    """Remove a plan ID from comparison. Returns True if removed, False if not found."""
    comparison = get_comparison_plan_ids(request)
    if plan_id in comparison:
        comparison.remove(plan_id)
        request.session["comparison_plans"] = comparison
        request.session.modified = True
        return True
    return False


def clear_comparison(request: HttpRequest) -> None:
    """Clear all plans from comparison."""
    request.session["comparison_plans"] = []
    request.session.modified = True


def is_in_comparison(request: HttpRequest, plan_id: int) -> bool:
    """Check if a plan is in comparison list."""
    return plan_id in get_comparison_plan_ids(request)


def track_viewed_plan(request: HttpRequest, plan_id: int, max_recent: int = 10) -> None:
    """Track a viewed plan in session."""
    recent = request.session.get("recently_viewed", [])
    
    # Remove if already in list (to move to front)
    if plan_id in recent:
        recent.remove(plan_id)
    
    # Add to front
    recent.insert(0, plan_id)
    
    # Keep only max_recent items
    recent = recent[:max_recent]
    
    request.session["recently_viewed"] = recent
    request.session.modified = True


def get_recently_viewed_ids(request: HttpRequest) -> list[int]:
    """Get list of recently viewed plan IDs."""
    return request.session.get("recently_viewed", [])
