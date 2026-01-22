"""
Context processors for plans app.
Makes saved plans and comparison counts available in all templates.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest

from . import session_utils


def plans_context(request: HttpRequest) -> dict:
    """Add saved plans and comparison data to all templates."""
    return {
        "saved_plan_count": len(session_utils.get_saved_plan_ids(request)),
        "comparison_count": len(session_utils.get_comparison_plan_ids(request)),
    }
